// With many thanks to the example code from
// https://github.com/spaceapegames/terraform-provider-example
package main

import (
	"terraform-provider-shakenfist/provider"

	"github.com/hashicorp/terraform/plugin"
)

func main() {
	plugin.Serve(&plugin.ServeOpts{
		ProviderFunc: provider.Provider,
	})
}
