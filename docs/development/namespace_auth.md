Namespace Context and Authentication
====================================

Namespaces
----------
* All resources (instances/networks/interfaces) are assigned to a Namespace.
* All requests to Shaken Fist have a Namespace context.
* Only requests in the "system" Namespace are able to access resources in other (foreign) Namespaces.
* The Namespace "system" is reserved.


Authentication
--------------
* A Namespace is accessed by supplying a valid "Key" (password).
* Namespaces can have multiple Keys.
* Each Key has a label referred to as a "Key Name".
* The Key Name is not specified during authentication.
* The Key Name "service_key" is reserved.


API Request Authentication
--------------------------
The authentication endpoint ```/auth``` is used to obtain a token to authenticate future API interaction.

To obtain the token, the authentication request is made specifying the Namespace and the Key. The Key Name is not required (nor important). The response contains the (JWT) token to be used as a Bearer token for the actual API request.

Internally, Shaken Fist determines the Namespace of each API request from the token.

Authentication tokens expire after a fixed period of time (nominally 15 minutes).


Interaction
-----------
Name spaces can be created from within the "system" Namespace. The creation of a Namespace requires that a Key and it's Key Name are specified with the creation request.

Keys do not have to be unique. A Key collision within a Namespace has no security consequences.

Key Names are only unique within a Namespace.

The purpose of the Key Name is to supply a handle to enable deletion of a Key. Actions are not logged against Key Names.


Inter-Node Authentication
-------------------------
Requests between Shaken Fist nodes use the same authentication system as external API requests.

When a node makes an API request to another node, the originating node will create (or reuse) a "service key" specific to the Namespace of the original request.

When a request is made from the "system" Namespace for a resource in a different Namespace, the API request is made using the foreign Namespace and the foreign Namespace service key.
