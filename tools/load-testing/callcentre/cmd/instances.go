package cmd

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"strconv"

	template "github.com/alecthomas/template"
	client "github.com/shakenfist/client-go"
)

type CloudFileData struct {
	MachineIndex int
	CCHost       string // CallCentre server FQDN or IP
}

func startInstances(userClient *client.Client, started chan Machine,
	networkUUID string) {

	// Load user data file template
	initTemplate, err := template.ParseFiles(cloudInitFilename)
	if err != nil {
		fmt.Println("Cannot load template file:", err)
		return
	}

	// Start the instances
	for i := 0; i < count; i++ {
		go startOneInstance(i, started, userClient, initTemplate, networkUUID)
	}
	fmt.Printf("\n--> All start requests sent\n")
}

func startOneInstance(index int, started chan Machine,
	userClient *client.Client, tmpl *template.Template, networkUUID string) {

	// Load the cloud-init userdata template file
	fileData := CloudFileData{
		MachineIndex: index,
		CCHost:       serverIP,
	}

	// Insert the data into the template file
	var initFile bytes.Buffer
	err := tmpl.Execute(&initFile, fileData)
	if err != nil {
		panic("Cannot parse template file:" + err.Error())
	}

	var inst client.Instance
	for {
		inst, err = userClient.CreateInstance(
			"CallHome-"+strconv.Itoa(index),
			cpu,
			memory,
			[]client.NetworkSpec{
				{
					NetworkUUID: networkUUID,
				},
			},
			[]client.DiskSpec{
				{
					Base: "ubuntu",
					Size: 8,
					Type: "disk",
				},
			},
			client.VideoSpec{
				Model:  "cirrus",
				Memory: 16384,
			},
			"",
			base64.StdEncoding.EncodeToString(initFile.Bytes()))

		if err == nil {
			break
		}
		fmt.Printf("Error starting instance %d: %v\n", index, err)
	}

	fmt.Printf(
		"  Started Instance %3d: %s  Node: %s  ConsolePort: %d\n",
		index, inst.UUID, inst.Node, inst.ConsolePort)

	started <- Machine{
		Index: index,
		UUID:  inst.UUID,
		Node:  inst.Node,
	}
}
