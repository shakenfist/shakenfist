// With many thanks to the example code from
// https://github.com/spaceapegames/terraform-provider-example
package provider

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/hashicorp/terraform/helper/schema"
	"github.com/mikalstill/shakenfist_go"
)

func validateNetblock(v interface{}, k string) (ws []string, es []error) {
	var errs []error
	var warns []string

	value, ok := v.(string)
	if !ok {
		errs = append(errs, fmt.Errorf("Expected name to be string"))
		return warns, errs
	}

	netblock := regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$`)
	if !netblock.Match([]byte(value)) {
		errs = append(errs, fmt.Errorf("Netblock must be IPv4 CIDR. Got %s", value))
		return warns, errs
	}
	return warns, errs
}

func resourceNetwork() *schema.Resource {
	fmt.Print()
	return &schema.Resource{
		Schema: map[string]*schema.Schema{
			"name": {
				Type:        schema.TypeString,
				Required:    true,
				Description: "The name of the network",
				ForceNew:    true,
			},
			"uuid": {
				Type:        schema.TypeString,
				Computed:    true,
				Description: "The UUID of the network",
			},
			"netblock": {
				Type:         schema.TypeString,
				Required:     true,
				Description:  "The CIDR IP range of the network",
				ForceNew:     true,
				ValidateFunc: validateNetblock,
			},
			"provide_dhcp": {
				Type:        schema.TypeBool,
				Required:    true,
				Description: "Should DHCP services exist on the network?",
				ForceNew:    true,
			},
			"provide_nat": {
				Type:        schema.TypeBool,
				Required:    true,
				Description: "Should NAT services exist on the network?",
				ForceNew:    true,
			},
		},
		Create: resourceCreateNetwork,
		Read:   resourceReadNetwork,
		Delete: resourceDeleteNetwork,
		Exists: resourceExistsNetwork,
		Importer: &schema.ResourceImporter{
			State: schema.ImportStatePassthrough,
		},
	}
}

func resourceCreateNetwork(d *schema.ResourceData, m interface{}) error {
	apiClient := m.(*shakenfist_go.Client)

	network, err := apiClient.CreateNetwork(
		d.Get("netblock").(string), d.Get("provide_dhcp").(bool),
		d.Get("provide_nat").(bool), d.Get("name").(string))
	if err != nil {
		return err
	}

	d.Set("uuid", network.UUID)
	d.SetId(network.UUID)
	return nil
}

func resourceReadNetwork(d *schema.ResourceData, m interface{}) error {
	apiClient := m.(*shakenfist_go.Client)

	network, err := apiClient.GetNetwork(d.Id())
	if err != nil {
		return err
	}

	d.Set("uuid", network.UUID)
	d.Set("name", network.Name)
	d.Set("netblock", network.NetBlock)
	d.Set("provide_dhcp", network.ProvideDHCP)
	d.Set("provide_nat", network.ProvideNAT)
	d.SetId(network.UUID)
	return nil
}

func resourceDeleteNetwork(d *schema.ResourceData, m interface{}) error {
	apiClient := m.(*shakenfist_go.Client)

	err := apiClient.DeleteNetwork(d.Id())
	if err != nil {
		return err
	}
	d.SetId("")
	return nil
}

func resourceExistsNetwork(d *schema.ResourceData, m interface{}) (bool, error) {
	apiClient := m.(*shakenfist_go.Client)

	_, err := apiClient.GetNetwork(d.Id())
	if err != nil {
		if strings.Contains(err.Error(), "not found") {
			return false, nil
		} else {
			return false, err
		}
	}
	return true, nil
}
