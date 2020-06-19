package main

import (
	"fmt"
	"time"

	"github.com/mikalstill/shakenfist_go"
)

func printNetwork(network shakenfist_go.Network) {
	fmt.Printf("UUID: %s\n", network.UUID)
	fmt.Printf("Name: %s\n", network.Name)
	fmt.Printf("Net Block: %s\n", network.NetBlock)
	fmt.Printf("Provide DHCP: %t\n", network.ProvideDHCP)
	fmt.Printf("Provide NAT: %t\n", network.ProvideNAT)
	fmt.Printf("Owner: %s\n", network.Owner)
	fmt.Printf("Floating Gateway: %s\n", network.FloatingGateway)
	fmt.Printf("State: %s\n", network.State)
	fmt.Printf("StateUpdated: %s\n", time.Unix(int64(network.StateUpdated), 0))
	fmt.Println("")
}

func printInstance(instance shakenfist_go.Instance) {
	fmt.Printf("UUID: %s\n", instance.UUID)
	fmt.Printf("Name: %s\n", instance.Name)
	fmt.Printf("CPUs: %d\n", instance.CPUs)
	fmt.Printf("Memory (MB): %d\n", instance.Memory)
	fmt.Println("Disks:")
	for _, disk := range instance.DiskSpecs {
		fmt.Printf("  - Base: %s\n", disk.Base)
		fmt.Printf("    Size: %d\n", disk.Size)
		fmt.Printf("    Bus:  %s\n", disk.Bus)
		fmt.Printf("    Type: %s\n", disk.Type)
	}
	fmt.Printf("SSHKey: %s\n", instance.SSHKey)
	fmt.Printf("Node: %s\n", instance.Node)
	fmt.Printf("ConsolePort: %d\n", instance.ConsolePort)
	fmt.Printf("VDIPort: %d\n", instance.VDIPort)
	fmt.Printf("UserData: %s\n", instance.UserData)
	fmt.Printf("State: %s\n", instance.State)
	fmt.Printf("StateUpdated: %s\n", time.Unix(int64(instance.StateUpdated), 0))
	fmt.Println("")
}

func printInterfaces(interfaces []shakenfist_go.NetworkInterface) {
	for _, iface := range interfaces {
		fmt.Printf("  - UUID: %s\n", iface.UUID)
		fmt.Printf("    Network UUID: %s\n", iface.NetworkUUID)
		fmt.Printf("    Instance UUID: %s\n", iface.InstanceUUID)
		fmt.Printf("    MAC Address: %s\n", iface.MACAddress)
		fmt.Printf("    IPv4 Address: %s\n", iface.IPv4)
		fmt.Printf("    Order: %d\n", iface.Order)
		fmt.Printf("    Floating Address: %s\n", iface.Floating)
		fmt.Printf("    State: %s\n", iface.State)
		fmt.Printf("    StateUpdated: %s\n", time.Unix(int64(iface.StateUpdated), 0))
		fmt.Printf("    Model: %s\n", iface.Model)
	}

	fmt.Println("")
}

func main() {
	c := shakenfist_go.NewClient("http://localhost", 13000)

	// --------------------------------------------------------------------------
	fmt.Println("**********************")
	fmt.Println("*** Make a network ***")
	fmt.Println("**********************")
	createdNetwork, err := c.CreateNetwork("192.168.50.0/24", true, true, "golang")
	if err != nil {
		fmt.Println("CreateNetwork request error: ", err)
		return
	}
	printNetwork(createdNetwork)

	// --------------------------------------------------------------------------
	fmt.Println("******************************")
	fmt.Println("*** Get a list of networks ***")
	fmt.Println("******************************")
	networks, err := c.GetNetworks()
	if err != nil {
		fmt.Println("GetNetworks request error: ", err)
		return
	}

	for _, network := range networks {
		printNetwork(network)
	}

	// --------------------------------------------------------------------------
	fmt.Println("************************")
	fmt.Println("*** Delete a network ***")
	fmt.Println("************************")
	err = c.DeleteNetwork(createdNetwork.UUID)
	if err != nil {
		fmt.Println("DeleteNetwork request error: ", err)
		return
	}

	// --------------------------------------------------------------------------
	fmt.Println("******************************")
	fmt.Println("*** Get a list of networks ***")
	fmt.Println("******************************")
	networks, err = c.GetNetworks()
	if err != nil {
		fmt.Println("GetNetworks request error: ", err)
		return
	}

	for _, network := range networks {
		printNetwork(network)
	}

	// --------------------------------------------------------------------------
	fmt.Println("******************************")
	fmt.Println("*** Get a specific network ***")
	fmt.Println("******************************")
	network, err := c.GetNetwork(networks[0].UUID)
	if err != nil {
		fmt.Println("GetNetwork request error: ", err)
		return
	}

	fmt.Printf("Fetched %s\n", network.Name)
	networkUuid := networks[0].UUID

	// --------------------------------------------------------------------------
	fmt.Println("*******************************")
	fmt.Println("*** Get a list of instances ***")
	fmt.Println("*******************************")
	instances, err := c.GetInstances()
	if err != nil {
		fmt.Println("GetInstances request error: ", err)
		return
	}

	for _, instance := range instances {
		printInstance(instance)
	}

	// --------------------------------------------------------------------------
	fmt.Println("**************************************************")
	fmt.Println("*** Get a specific instance and its interfaces ***")
	fmt.Println("**************************************************")
	instance, err := c.GetInstance(instances[0].UUID)
	if err != nil {
		fmt.Println("GetInstance request error: ", err)
		return
	}
	fmt.Printf("Fetched %s\n", instance.Name)

	// --------------------------------------------------------------------------
	interfaces, err := c.GetInstanceInterfaces(instances[0].UUID)
	if err != nil {
		fmt.Println("GetInstanceInterfaces request error: ", err)
		return
	}
	printInterfaces(interfaces)

	// --------------------------------------------------------------------------
	fmt.Println("**************************")
	fmt.Println("*** Create an instance ***")
	fmt.Println("**************************")
	instance, err = c.CreateInstance("golang", 1, 1,
		[]shakenfist_go.NetworkSpec{{NetworkUUID: networkUuid}},
		[]shakenfist_go.DiskSpec{{Base: "cirros", Size: 8, Type: "disk", Bus: ""}},
		"", "")
	if err != nil {
		fmt.Println("CreateInstance request error: ", err)
		return
	}
	printInstance(instance)

	// --------------------------------------------------------------------------
	fmt.Println("**************************")
	fmt.Println("*** Float the instance ***")
	fmt.Println("**************************")
	interfaces, err = c.GetInstanceInterfaces(instance.UUID)
	if err != nil {
		fmt.Println("GetInstanceInterfaces request error: ", err)
		return
	}

	err = c.FloatInterface(interfaces[0].UUID)
	if err != nil {
		fmt.Println("FloatInterface request error: ", err)
		return
	}

	interfaces, err = c.GetInstanceInterfaces(instance.UUID)
	if err != nil {
		fmt.Println("GetInstanceInterfaces request error: ", err)
		return
	}
	fmt.Println("Interfaces:")
	printInterfaces(interfaces)

	err = c.DefloatInterface(interfaces[0].UUID)
	if err != nil {
		fmt.Println("DefloatInterface request error: ", err)
		return
	}

	interfaces, err = c.GetInstanceInterfaces(instance.UUID)
	if err != nil {
		fmt.Println("GetInstanceInterfaces request error: ", err)
		return
	}
	fmt.Println("Interfaces:")
	printInterfaces(interfaces)

	// --------------------------------------------------------------------------
	fmt.Println("**************************")
	fmt.Println("*** Delete an instance ***")
	fmt.Println("**************************")
	err = c.DeleteInstance(instance.UUID)
	if err != nil {
		fmt.Println("DeleteInstance request error: ", err)
		return
	}
}
