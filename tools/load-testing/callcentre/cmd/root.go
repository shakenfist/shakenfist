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
	instanceLoad      int
	cpu               int
	delay             int
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
  1. Create a unique namespace
  2. Create a network within that namespace
  3. Start instances in the namespace
  4. Wait for each instance to call back via HTTP on port 8089
  5. When all instances have called back, delete all instances
  6. Delete the network
  7. Delete the namespace

If the test fails, the "sf-client namespace clean" command can be used to
remove any remaining instances and networks.`,
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

	rootCmd.PersistentFlags().IntVar(&instanceLoad, "load",
		0, "Number of instances to start")
	rootCmd.PersistentFlags().IntVar(&cpu, "cpu",
		0, "Instance CPU count")
	rootCmd.PersistentFlags().BoolVar(&deleteOnCallback, "delCallback",
		false, "Delete instance immediately after it's callback")
	rootCmd.PersistentFlags().IntVar(&memory, "memory",
		0, "Instance Memory size in MB")
	rootCmd.PersistentFlags().StringVar(&serverIP, "ip",
		"", "This servers reachable IP address from within the instances")
	rootCmd.PersistentFlags().IntVar(&delay, "delay",
		1, "Delay between attempting to start instances")

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
	Index       int
	UUID        string
	Node        string
	ConsolePort int
}

// runRootCmd is the actual code that executes desired load test
func runRootCmd() {
	fmt.Printf("Callcentre Load Test:  %d Instances of CPU=%d  Memory=%d\n\n",
		instanceLoad, cpu, memory)

	// Check parameters
	if instanceLoad <= 0 {
		fmt.Println("Load should be greater than 0")
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
	fmt.Println("Creating network:", uniqueName)
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
	fmt.Printf("Starting instances...\n\n")
	instanceStart := make(chan Machine)
	go startInstances(instanceLoad, userClient, delay, instanceStart,
		network.UUID)

	// Loop waiting for HTTP callbacks from each instance.
	Machines := make(map[int]Machine)
	callbackCount := 0
	started := 0

forever:
	for {
		// Timer to delay updating list of expected callbacks
		delay := time.After(5 * time.Second)

		select {
		case m := <-instanceStart:
			Machines[m.Index] = m
			started += 1
			fmt.Printf(
				"  Started Instance %3d: %s  Node: %s  ConsolePort: %d\n",
				m.Index, m.UUID, m.Node, m.ConsolePort)

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
			if started == instanceLoad && len(Machines) == 0 {
				fmt.Println("\nSUCCESS - All instances have phoned home")
				break forever
			}

		case <-delay:
			fmt.Printf("\nInstances: Started=%d Callbacks=%d Outstanding=%d\n",
				started, callbackCount, len(Machines))
			for i := range Machines {
				fmt.Printf("  %d: %s  %s  %d\n",
					i, Machines[i].UUID, Machines[i].Node,
					Machines[i].ConsolePort)
			}
		}
	}

	fmt.Println("Cleaning up...")

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
