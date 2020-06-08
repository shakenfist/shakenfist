// With many thanks to the example code from
// https://github.com/spaceapegames/terraform-provider-example
package provider

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/hashicorp/terraform/helper/schema"
	"github.com/mikalstill/shakenfist_go"
)

func resourceInstance() *schema.Resource {
	fmt.Print()
	return &schema.Resource{
		Schema: map[string]*schema.Schema{
			"name": {
				Type:        schema.TypeString,
				Required:    true,
				Description: "The name of the instance",
				ForceNew:    true,
			},
			"uuid": {
				Type:        schema.TypeString,
				Computed:    true,
				Description: "The UUID of the instance",
			},
			"cpus": {
				Type:        schema.TypeInt,
				Required:    true,
				Description: "The number of CPUs for the instance",
				ForceNew:    true,
			},
			"memory": {
				Type:        schema.TypeInt,
				Required:    true,
				Description: "The amount of RAM for the instance in GB",
				ForceNew:    true,
			},
			"disks": {
				Type:     schema.TypeList,
				Required: true,
				ForceNew: true,
				Elem: &schema.Schema{
					Type: schema.TypeString,
				},
			},
			"networks": {
				Type:     schema.TypeList,
				Required: true,
				ForceNew: true,
				Elem: &schema.Schema{
					Type: schema.TypeString,
				},
			},
			"ssh_key": {
				Type:        schema.TypeString,
				Optional:    true,
				Computed:    true,
				Description: "The ssh key to embed into the instance via config drive",
			},
			"user_data": {
				Type:        schema.TypeString,
				Optional:    true,
				Computed:    true,
				Description: "User data to pass to the instance via config drive, encoded as base64",
			},
		},
		Create: resourceCreateInstance,
		Read:   resourceReadInstance,
		Delete: resourceDeleteInstance,
		Exists: resourceExistsInstance,
		Importer: &schema.ResourceImporter{
			State: schema.ImportStatePassthrough,
		},
	}
}

func resourceCreateInstance(d *schema.ResourceData, m interface{}) error {
	apiClient := m.(*shakenfist_go.Client)

	var disks []shakenfist_go.DiskSpec
	for _, disk := range d.Get("disks").([]interface{}) {
		var diskSpec shakenfist_go.DiskSpec
		for _, diskElem := range strings.Split(disk.(string), ",") {
			elems := strings.Split(diskElem, "=")
			if elems[0] == "base" {
				diskSpec.Base = elems[1]
			} else if elems[0] == "size" {
				diskSpec.Size, _ = strconv.Atoi(elems[1])
			} else if elems[0] == "bus" {
				diskSpec.Bus = elems[1]
			} else if elems[0] == "type" {
				diskSpec.Type = elems[1]
			}
		}
		disks = append(disks, diskSpec)
	}

	var networks []shakenfist_go.NetworkSpec
	for _, net := range d.Get("networks").([]interface{}) {
		var netSpec shakenfist_go.NetworkSpec
		for _, netElem := range strings.Split(net.(string), ",") {
			elems := strings.Split(netElem, "=")
			if elems[0] == "uuid" {
				netSpec.NetworkUUID = elems[1]
			} else if elems[0] == "address" {
				netSpec.Address = elems[1]
			} else if elems[0] == "macaddress" {
				netSpec.MACAddress = elems[1]
			} else if elems[0] == "model" {
				netSpec.Model = elems[1]
			}
		}
		networks = append(networks, netSpec)
	}

	inst, err := apiClient.CreateInstance(d.Get("name").(string), d.Get("cpus").(int),
		d.Get("memory").(int), networks, disks, d.Get("ssh_key").(string),
		d.Get("user_data").(string))
	if err != nil {
		return err
	}

	d.Set("uuid", inst.UUID)
	d.SetId(inst.UUID)
	return nil
}

func resourceReadInstance(d *schema.ResourceData, m interface{}) error {
	apiClient := m.(*shakenfist_go.Client)

	inst, err := apiClient.GetInstance(d.Id())
	if err != nil {
		return err
	}

	d.Set("uuid", inst.UUID)
	d.Set("name", inst.Name)
	d.Set("cpus", inst.CPUs)
	d.Set("memory", inst.Memory)
	d.Set("disks", inst.DiskSpecs)
	d.Set("ssh_key", inst.SSHKey)
	d.Set("node", inst.Node)
	d.Set("console_port", inst.ConsolePort)
	d.Set("vdi_port", inst.VDIPort)
	d.Set("user_data", inst.UserData)
	d.SetId(inst.UUID)
	return nil
}

func resourceDeleteInstance(d *schema.ResourceData, m interface{}) error {
	apiClient := m.(*shakenfist_go.Client)

	err := apiClient.DeleteInstance(d.Id())
	if err != nil {
		return err
	}
	d.SetId("")
	return nil
}

func resourceExistsInstance(d *schema.ResourceData, m interface{}) (bool, error) {
	apiClient := m.(*shakenfist_go.Client)

	_, err := apiClient.GetInstance(d.Id())
	if err != nil {
		if strings.Contains(err.Error(), "not found") {
			return false, nil
		} else {
			return false, err
		}
	}
	return true, nil
}
