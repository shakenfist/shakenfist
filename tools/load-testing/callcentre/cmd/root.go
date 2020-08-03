package cmd

import (
	"crypto/rand"
	"fmt"
	"log"
	"os"
	"time"

	client "github.com/shakenfist/client-go"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"

	"callcentre/callback"
)

var (
	cfgFile           string
	count             int
	cpu               int
	memory            int
	cloudInitFilename string
	serverIP          string
	deleteOnCallback  bool
)

// rootCmd represents the base command when called without any subcommands
var rootCmd = &cobra.Command{
	Use:   "callcentre",
	Short: "Call Centre",
	Long: `Call Centre will:
  Create a unique namespace
  Create a network within that namespace
  Start instances in the namespace
  Wait for each instance to call back via HTTP on port 8089
  Delete each instance that has called back
  When all instances have called back, delete the network
  Delete the namespace

If the test fails, the "sf-client namespace clean" command can be used`,
	Run: func(cmd *cobra.Command, args []string) {
		runRootCmd()
	},
}

func Execute() {
	// Actually run the command
	if err := rootCmd.Execute(); err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
}

func init() {
	cobra.OnInitialize(initConfig)

	rootCmd.PersistentFlags().StringVar(&cfgFile, "config",
		"", "Call Centre config file (default is callcentre.yaml)")

	rootCmd.PersistentFlags().IntVar(&count, "count",
		0, "Number of instances to start")
	rootCmd.PersistentFlags().IntVar(&cpu, "cpu",
		0, "Instance CPU count")
	rootCmd.PersistentFlags().BoolVar(&deleteOnCallback, "delCallback",
		false, "Delete instance immediately after it's callback")
	rootCmd.PersistentFlags().IntVar(&memory, "memory",
		0, "Instance Memory size in MB")
	rootCmd.PersistentFlags().StringVar(&serverIP, "ip",
		"", "This servers reachable IP address from within the instances")

	rootCmd.PersistentFlags().StringVar(&cloudInitFilename, "cloudinit",
		"phone-home.yaml", "Cloud init phone home YAML filename")

	// Enable the use of environment variables
	if err := viper.BindPFlags(rootCmd.PersistentFlags()); err != nil {
		log.Fatal(err)
	}
}

// initConfig reads in config file and ENV variables if set.
func initConfig() {
	viper.SetEnvPrefix("CC_")

	if cfgFile != "" {
		// Use config file from the flag.
		viper.SetConfigFile(cfgFile)
	} else {
		viper.AddConfigPath(".")
		viper.SetConfigName("callcentre")
	}

	viper.AutomaticEnv() // read in environment variables that match

	// If a config file is found, read it in.
	if err := viper.ReadInConfig(); err == nil {
		fmt.Println("Using config file:", viper.ConfigFileUsed())
	}
}

type Machine struct {
	Index int
	UUID  string
	Node  string
}

// runRootCmd is the actual code that executes desired load test
func runRootCmd() {
	fmt.Printf("CPU=%d  Memory=%d\n", cpu, memory)
	fmt.Printf("Starting %d instances...\n\n", count)

	// Check parameters
	if count <= 0 {
		fmt.Println("Count should be greater than 0")
		return
	}
	if cpu <= 0 {
		fmt.Println("Instance CPU should be greater than 0")
		return
	}
	if memory <= 0 {
		fmt.Println("Instance memory should be greater than 0")
		return
	}
	if serverIP == "" {
		fmt.Println("Callback IP must be set")
		return
	}

	// Create load test namespace
	sysClient := client.NewClient(
		os.Getenv("SHAKENFIST_API_URL"),
		os.Getenv("SHAKENFIST_NAMESPACE"),
		os.Getenv("SHAKENFIST_KEY"),
	)

	uniqueName := "loadtest-" + genRandHex(4)
	fmt.Println("Creating new load test namespace:", uniqueName)
	if err := sysClient.CreateNamespace(uniqueName); err != nil {
		fmt.Printf("Unable to create new namespace:%v\n", err)
		return
	}

	// Create unique key
	userKey := genRandHex(40)
	err := sysClient.CreateNamespaceKey(uniqueName, "loadtest", userKey)
	if err != nil {
		fmt.Printf("Unable to create user namespace key: %v", err)
		return
	}

	// Use the new user namespace
	userClient := client.NewClient(os.Getenv("SHAKENFIST_API_URL"),
		uniqueName, userKey)

	// Create new network
	network, err := userClient.CreateNetwork("10.0.0.0/24",
		true, true, uniqueName)
	if err != nil {
		fmt.Printf("Unable to create network (%s): %v", uniqueName, err)
		return
	}

	// Start listening for HTTP callbacks
	cb := callback.NewCallBack()
	cb.StartServer()

	// Get instances started
	instanceStart := make(chan Machine)
	go startInstances(userClient, instanceStart, network.UUID)

	// Loop waiting for HTTP callbacks from each instance.
	Machines := make(map[int]Machine)
	callbackCount := 0
	started := 0

	for exit := false; !exit; {
		// Timer to delay updating list of expected callbacks
		delay := time.After(5 * time.Second)

		select {
		case m := <-instanceStart:
			Machines[m.Index] = m
			started += 1
			continue

		case id := <-cb.Received:
			fmt.Println("    Received callback:", id)
			callbackCount += 1

			if deleteOnCallback {
				if err := sysClient.DeleteInstance(Machines[id].UUID); err != nil {
					fmt.Printf("Error deleting instance (%s):%v\n",
						Machines[id].UUID, err)
				}
			}

			delete(Machines, id)
			if len(Machines) == 0 {
				fmt.Println("\nSUCCESS - All instances have phoned home")
				exit = true
			}
			continue

		case <-delay:
			fmt.Printf("\nInstances: Started=%d Callbacks=%d Outstanding=%d\n",
				started, callbackCount, len(Machines))
			for i := range Machines {
				fmt.Printf("  %d: %s  %s\n",
					i, Machines[i].UUID, Machines[i].Node)
			}
		}
	}

	// Ensure all machines are deleted
	_, err = sysClient.DeleteAllInstances(uniqueName)
	if err != nil {
		fmt.Printf("Error deleting all instances: %v\n", err)
	}

	// Delete SF network used for this load test
	if err = sysClient.DeleteNetwork(network.UUID); err != nil {
		fmt.Printf("Error deleting created network (%s): %v\n",
			network.UUID, err)
	}

	// Delete SF namespace used for this load test
	if err := sysClient.DeleteNamespace(uniqueName); err != nil {
		fmt.Printf("Error deleting created namespace (%s): %v\n",
			uniqueName, err)
	}
}

func genRandHex(len int) string {
	b := make([]byte, len)
	_, err := rand.Read(b)
	if err != nil {
		log.Fatal(err)
	}

	randHex := ""
	for i := 0; i < len; i++ {
		randHex += fmt.Sprintf("%x", b[i])
	}

	return randHex
}
