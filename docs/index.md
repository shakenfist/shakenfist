# Introduction

## Shaken Fist, an open cloud aimed at small and edge deployments

Shaken Fist is a deliberately opinionated cloud intended for small deployments. We
spend a lot of time trying to do the simplest possible thing well, and keep our
resource usage on idle deployments as low as possible. Shaken Fist has progressed
from being a proof of concept to being a functional cloud, although the release
numbers being below zero indicates that we are still stabilizing the REST API
and that breaking changes might occur. 1.0 will be released when we are relatively
sure of stability going forwards.

Shaken Fist started a personal research project, but has grown into something
which is starting to see wider contributions and some small commercial deployments.

## The underlying idea

Originally Shaken Fist came about as a reaction to the increasing complexity of
OpenStack, as well as a desire to experiment with alternative approaches to
solving the problems that OpenStack Compute addresses. What I really wanted was
a simple API to orchestrate virtual machines, but it needed to run with minimal
resource overhead and be simple to deploy. I also wanted it to always work in a
predictable way.

One of the reasons OpenStack is so complicated and its behaviour varies is because
it has many options to configure. The solution seemed obvious to me -- a cloud
that is super opinionated. For each different functional requirement there is
one option, and the simplest option is chosen where possible. Read on for some
examples.

## Development choices

If there is an existing library which does a thing, we use it. OpenStack suffered
from being old (and having issues with re-writes being hard), as well as licensing
constraints. We just use the code that others have provided to the community. Always.

## Deployment choices

libvirt is the only supported hypervisor. Instances are specified to libvirt with
simple templated XML. If your local requirements are different to what's in the
template, you're welcome to change the template to meet your needs. If your
template changes break things, you're also welcome to debug what went wrong for
yourself.

## "I'd like to subscribe to your newsletter"

First off, we don't have a newsletter. That said, updates will be announced here
as they happen. What we do have is useful links:

* [The Shaken Fist repository](http://github.com/shakenfist/shakenfist) is where
  the code for the server is, including the deployer. Its a good place to start.

* There is also [the python client repository](http://github.com/shakenfist/client-python)
  contains the python API client, as well as the command line client that users
  and shell scripts use to interact with Shaken Fist.

## What is Shaken Fist? Can I help?

The [Shaken Fist Manifesto](/manifesto/) is our general conceptual starting
point, ubt apart from that just reach out and have a chat.