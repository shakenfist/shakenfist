---
title: Introduction
---
# Shaken Fist, a minimal cloud aimed at small and edge deployments

Shaken Fist is a deliberately minimal cloud intended for small deployments. We spend a lot of time trying to do the simplest possible thing well, and keep our resource usage on idle deployments as low as possible. Shaken Fist also currently a work in progress, so take statements here with a grain of salt.

Shaken Fist started a personal research project, but has grown into something which is starting to see wider contributions and some small commercial deployments.

Here's a simple feature matrix:

| Feature                                           | Implemented | Planned | Not Planned |
|---------------------------------------------------|-------------|---------|-------------|
| Servers / instances                               | v0.1        |         |             |
| Networks                                          | v0.1        |         |             |
| Multiple NIC's for a given server                 | v0.1        |         |             |
| Pre-cache a server image                          | v0.1        |         |             |
| Floating IPs                                      | v0.1        |         |             |
| Pause                                             | v0.1        |         |             |
| Reboot (hard and soft)                            | v0.1        |         |             |
| Security groups                                   |             | Yes     |             |
| Text console                                      | v0.1        |         |             |
| VDI                                               | v0.1        |         |             |
| User data                                         | v0.1        |         |             |
| Keypairs                                          | v0.1        |         |             |
| Virtual networks allow overlapping IP allocations | v0.1        |         |             |
| REST API authentication and object ownership      | v0.2        |         |             |
| Snapshots (of all disks)                          | v0.1        |         |             |
| Central API service                               | v0.1        |         |             |
| Scheduling                                        | v0.1        |         |             |
| Volumes                                           |             |         | No plans    |
| Quotas                                            |             |         | No plans    |
| API versioning                                    |             |         | No plans    |
| Keystone style service lookup and URLs            |             |         | No plans    |
| Create multiple servers in a single request       |             |         | No plans    |
| Resize a server                                   |             |         | No plans    |
| Server groups                                     |             |         | No plans    |
| Change admin password                             |             |         | No plans    |
| Rebuild a server                                  |             |         | No plans    |
| Shelve / unshelve                                 |             |         | No plans    |
| Trigger crash dump                                |             |         | No plans    |
| Live migration                                    |             |         | No plans    |
| Flavors                                           |             |         | No plans    |
| Guest agents                                      |             |         | No plans    |
| Host aggregates                                   |             |         | No plans    |
| Server tags                                       | v0.2, we call them "metadata"  |             |
| ~~Persistence in MySQL~~                          | v0.1        |         |             |
| Distributed etcd for locking and persistence      | v0.2        |         |             |
| Production grade REST API via gunicorn            | v0.2        |         |             |
| Python REST API client                            | v0.1        |         |             |
| [golang REST API client](http://github.com/shakenfist/client-go) | v0.2        |         |             |
| [Terraform provider](http://github.com/shakenfist/terraform-provider-shakenfist) | v0.2        |         |             |

## The underlying idea

Originally Shaken Fist came about as a reaction to the increasing complexity of OpenStack, as well as a desire to experiment with alternative approaches to solving the problems that OpenStack Compute addresses. What I really wanted was a simple API to orchestrate virtual machines, but it needed to run with minimal resource overhead and be simple to deploy. I also wanted it to always work in a predictable way.

One of the reasons OpenStack is so complicated and its behaviour varies is because it has many options to configure. The solution seemed obvious to me -- a cloud that is super opinionated. For each different functional requirement there is one option, and the simplest option is chosen where possible. Read on for some examples.

## Development choices

If there is an existing library which does a thing, we use it. OpenStack suffered from being old (and having issues with re-writes being hard), as well as licensing constraints. We just use the code that others have provided to the community. Always.

## Deployment choices

libvirt is the only supported hypervisor. Instances are specified to libvirt with simple templated XML. If your local requirements are different to what's in the template, you're welcome to change the template to meet your needs. If your template changes break things, you're also welcome to debug what went wrong for yourself. We provide a sample Ansible based deployer in the `deploy` directory.

## Here's what I said when the project was first announced

*To give you a basic flavour of the intent of Shaken Fist, here's what I said when I first announced the project.*

For at least six months I’ve felt the desire for a simpler cloud orchestration layer — both for my own personal uses, and also as a test bed for ideas for what a smaller, simpler cloud might look like. My personal use case involves a relatively small environment which echoes what we now think of as edge compute — less than 10 RU of machines with a minimum of orchestration and management overhead.

At the time that I was thinking about these things, the Australian bush fires and COVID-19 came along, and presented me with a lot more spare time than I had expected to have. While I’m still blessed to be employed, all of my social activities have been cancelled, so I find myself at home at a loose end on weekends and evenings a lot more than before.

Thus Shaken Fist was born — named for a Simpson’s meme, Shaken Fist is a deliberately small and highly opinionated cloud implementation aimed at working well in small deployments such as homes, labs, edge compute locations, deployed systems, and so forth.

I’d taken a bit of trouble with each feature in Shaken Fist to think through what the simplest and highest value way of doing something is. For example, instances always get a config drive and there is no metadata server. There is also only one supported type of virtual networking, and one supported hypervisor. That said, this means Shaken Fist is less than 5,000 lines of code, and small enough that new things can be implemented very quickly by a single middle aged developer.

I think Shaken Fist is useful to others. Its Apache-2.0 licensed, and available on github if you’re interested.

## "I'd like to subscribe to your newsletter"

First off, we don't have a newsletter. That said, updates will be announced here as they happen. What we do have is useful links:

* [The Shaken Fist repository](http://github.com/shakenfist/shakenfist) is where the code for the server is, including the deployer. Its a good place to start.

* There are a series of client repositories as well:
    * [The python client repository](http://github.com/shakenfist/client-python) contains the python API client, as well as the command line client that users and shell scripts use to interact with Shaken Fist.
    * [The golang client repository](http://github.com/shakenfist/client-go) contains... wait for it... the golang client library. This is used by the terraform provider.
    * [The javascript client repository](http://github.com/shakenfist/client-js) contains... wait for it... a javascript client library. It should be noted that this client is currently incomplete.

* [The terraform provider repository](http://github.com/shakenfist/terraform-provider-shakenfist) has a Terraform provider for Shaken Fist which uses the golang client.

* [The ansible modules repository](http://github.com/shakenfist/ansible-modules) contains a few simple modules for using Shaken Fist in ansible roles and plays.

We also have a few "more internal" repositories:

* [The load test repository](http://github.com/shakenfist/loadtest) contains a simpler load tester we use for validating releases.

* [The reproducibles repository](http://github.com/shakenfist/reproducables) contains simple test cases for reproducing strange behaviours we have seen while developing Shaken Fist.

## What is Shaken Fist? Can I help?

[Shaken Fist Manifesto](manifesto.md)

## New user guides

We need more of these. For now, we have the following:

* [An installation guide](installation.md)
* [A user guide](usage.md), which is currently incomplete but better than nothing.

## Release changelogs

Version v0.2 was when we started taking releases and upgrades seriously. Thus, its also when we started writing changelogs:

* [v0.3 series](changelog/changelog_v03.md)
* [v0.2 series](changelog/changelog_v02.md)

## API documentation

There is some limited API documentation, its definitely a known gap.

* [The power states an instance can be in](power_states.md)

## Documentation for developers of Shaken Fist
* We have [release documentation](development/release_process.md). We found this mildly surprising too.
* Everyone is confused by the networking, so we wrote some [networking documentation](networking/overview.md). Hopefully it helps.
