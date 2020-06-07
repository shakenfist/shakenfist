// With many thanks to the example code from
// https://github.com/spaceapegames/terraform-provider-example
package shakenfist

// Note that the following API calls are not yet implemented as
// they are not needed for the terraform provider, which is the
// primary user of this client:
//
// * snapshot instance
// * get instance snapshots
// * reboot instance
// * power off / on instance
// * pause / unpause instance
// * get instance events
// * cache image
// * get network events
// * get nodes

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

// Client holds all of the information required to connect to
// the server
type Client struct {
	hostname   string
	port       int
	httpClient *http.Client
}

// NewClient returns a client ready for use
func NewClient(hostname string, port int) *Client {
	return &Client{
		hostname:   hostname,
		port:       port,
		httpClient: &http.Client{},
	}
}

// Network is a definition of a network
type Network struct {
	UUID            string  `json:"uuid"`
	Name            string  `json:"name"`
	VXId            int     `json:"vxid"`
	NetBlock        string  `json:"netblock"`
	ProvideDHCP     bool    `json:"provide_dhcp"`
	ProvideNAT      bool    `json:"provide_nat"`
	Owner           string  `json:"owner"`
	FloatingGateway string  `json:"floating_gateway"`
	State           string  `json:"state"`
	StateUpdated    float64 `json:"state_updated"`
}

// GetNetworks fetches a list of networks
func (c *Client) GetNetworks() ([]Network, error) {
	body, err := c.httpRequest("networks", "GET", bytes.Buffer{})
	if err != nil {
		return nil, err
	}

	networks := []Network{}
	err = json.NewDecoder(body).Decode(&networks)
	if err != nil {
		return nil, err
	}
	return networks, nil
}

// GetNetwork fetches a specific instance by UUID
func (c *Client) GetNetwork(networkUUID string) (Network, error) {
	path := fmt.Sprintf("networks/%s", networkUUID)
	body, err := c.httpRequest(path, "GET", bytes.Buffer{})
	if err != nil {
		return Network{}, err
	}

	network := Network{}
	err = json.NewDecoder(body).Decode(&network)
	if err != nil {
		return Network{}, err
	}
	return network, nil
}

type createNetworkRequest struct {
	Name        string `json:"name"`
	Netblock    string `json:"netblock"`
	ProvideDHCP bool   `json:"provide_dhcp"`
	ProvideNAT  bool   `json:"provide_nat"`
}

// CreateNetwork creates a new network
func (c *Client) CreateNetwork(netblock string, provideDHCP bool, provideNAT bool,
	name string) (Network, error) {
	request := &createNetworkRequest{
		Netblock:    netblock,
		ProvideDHCP: provideDHCP,
		ProvideNAT:  provideNAT,
		Name:        name,
	}
	post, err := json.Marshal(request)
	if err != nil {
		return Network{}, err
	}

	body, err := c.httpRequest("networks", "POST", *bytes.NewBuffer(post))
	if err != nil {
		return Network{}, err
	}

	network := Network{}
	err = json.NewDecoder(body).Decode(&network)
	if err != nil {
		return Network{}, err
	}
	return network, nil
}

// DeleteNetwork removes a network with a specified UUID
func (c *Client) DeleteNetwork(networkUUID string) error {
	path := fmt.Sprintf("networks/%s", networkUUID)
	_, err := c.httpRequest(path, "DELETE", bytes.Buffer{})
	if err != nil {
		return err
	}
	return nil
}

// DiskSpec is a definition of an instance disk
type DiskSpec struct {
	Base string `json:"base"`
	Size int    `json:"size"`
	Bus  string `json:"bus"`
	Type string `json:"type"`
}

// NetworkSpec is a definition of an instance network connect
type NetworkSpec struct {
	NetworkUUID string `json:"network_uuid"`
	Address     string `json:"address"`
	MACAddress  string `json:"macaddress"`
	Model       string `json:"model"`
}

// Instance is a definition of an instance
type Instance struct {
	UUID         string                 `json:"uuid"`
	Name         string                 `json:"name"`
	CPUs         int                    `json:"cpus"`
	Memory       int                    `json:"memory"`
	DiskSpecs    []DiskSpec             `json:"disk_spec"`
	SSHKey       string                 `json:"ssh_key"`
	Node         string                 `json:"node"`
	ConsolePort  int                    `json:"console_port"`
	VDIPort      int                    `json:"vdi_port"`
	UserData     string                 `json:"User_data"`
	BlockDevices map[string]interface{} `json:"block_devices"`
	State        string                 `json:"state"`
	StateUpdated float64                `json:"state_updated"`
}

// GetInstances fetches a list of instances
func (c *Client) GetInstances() ([]Instance, error) {
	body, err := c.httpRequest("instances", "GET", bytes.Buffer{})
	if err != nil {
		return nil, err
	}

	instances := []Instance{}
	err = json.NewDecoder(body).Decode(&instances)
	if err != nil {
		return nil, err
	}
	return instances, nil
}

// GetInstance fetches a specific instance by UUID
func (c *Client) GetInstance(instanceUUID string) (Instance, error) {
	path := fmt.Sprintf("instances/%s", instanceUUID)
	body, err := c.httpRequest(path, "GET", bytes.Buffer{})
	if err != nil {
		return Instance{}, err
	}

	instance := Instance{}
	err = json.NewDecoder(body).Decode(&instance)
	if err != nil {
		return Instance{}, err
	}
	return instance, nil
}

// NetworkInterface is a definition of an network interface for an instance
type NetworkInterface struct {
	UUID         string  `json:"uuid"`
	NetworkUUID  string  `json:"network_uuid"`
	InstanceUUID string  `json:"instance_uuid"`
	MACAddress   string  `json:"macaddr"`
	IPv4         string  `json:"ipv4"`
	Order        int     `json:"order"`
	Floating     string  `json:"floating"`
	State        string  `json:"state"`
	StateUpdated float64 `json:"state_updated"`
	Model        string  `json:"model"`
}

// GetInstanceInterfaces fetches a list of network interfaces for an instance
func (c *Client) GetInstanceInterfaces(instanceUUID string) ([]NetworkInterface, error) {
	path := fmt.Sprintf("instances/%s/interfaces", instanceUUID)
	body, err := c.httpRequest(path, "GET", bytes.Buffer{})
	if err != nil {
		return nil, err
	}

	interfaces := []NetworkInterface{}
	err = json.NewDecoder(body).Decode(&interfaces)
	if err != nil {
		return nil, err
	}
	return interfaces, nil
}

type createInstanceRequest struct {
	Name     string        `json:"name"`
	CPUs     int           `json:"cpus"`
	Memory   int           `json:"memory"`
	Network  []NetworkSpec `json:"network"`
	Disk     []DiskSpec    `json:"disk"`
	SSHKey   string        `json:"ssh_key"`
	UserData string        `json:"user_data"`
}

// CreateInstance creates a new instance
func (c *Client) CreateInstance(Name string, CPUs int, Memory int,
	Networks []NetworkSpec, Disks []DiskSpec, SSHKey string,
	UserData string) (Instance, error) {
	request := &createInstanceRequest{
		Name:     Name,
		CPUs:     CPUs,
		Memory:   Memory,
		Network:  Networks,
		Disk:     Disks,
		SSHKey:   SSHKey,
		UserData: UserData,
	}
	post, err := json.Marshal(request)
	if err != nil {
		return Instance{}, err
	}

	body, err := c.httpRequest("instances", "POST", *bytes.NewBuffer(post))
	if err != nil {
		return Instance{}, err
	}

	instance := Instance{}
	err = json.NewDecoder(body).Decode(&instance)
	if err != nil {
		return Instance{}, err
	}
	return instance, nil
}

// DeleteInstance deletes an instance
func (c *Client) DeleteInstance(instanceUUID string) error {
	path := fmt.Sprintf("instances/%s", instanceUUID)
	_, err := c.httpRequest(path, "DELETE", bytes.Buffer{})
	if err != nil {
		return err
	}
	return nil
}

// FloatInterface adds a floating IP to an interface
func (c *Client) FloatInterface(interfaceUUID string) error {
	path := fmt.Sprintf("interfaces/%s/float", interfaceUUID)
	_, err := c.httpRequest(path, "POST", bytes.Buffer{})
	if err != nil {
		return err
	}
	return nil
}

// DefloatInterface removes a floating IP from an interface
func (c *Client) DefloatInterface(interfaceUUID string) error {
	path := fmt.Sprintf("interfaces/%s/defloat", interfaceUUID)
	_, err := c.httpRequest(path, "POST", bytes.Buffer{})
	if err != nil {
		return err
	}
	return nil
}

func (c *Client) httpRequest(path, method string, body bytes.Buffer) (closer io.ReadCloser, err error) {
	req, err := http.NewRequest(method, c.requestPath(path), &body)
	if err != nil {
		return nil, err
	}

	req.Header.Add("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode != http.StatusOK {
		respBody := new(bytes.Buffer)
		_, err := respBody.ReadFrom(resp.Body)
		if err != nil {
			return nil, fmt.Errorf("Got a non 200 status code: %v", resp.StatusCode)
		}
		return nil, fmt.Errorf("Got a non 200 status code: %v - %s", resp.StatusCode, respBody.String())
	}
	return resp.Body, nil
}

func (c *Client) requestPath(path string) string {
	return fmt.Sprintf("%s:%v/%s", c.hostname, c.port, path)
}
