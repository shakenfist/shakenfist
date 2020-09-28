---
title: Introduction
---
# Shaken Fist, a minimal cloud aimed at small and edge deployments

For at least six months I’ve felt the desire for a simpler cloud orchestration layer — both for my own personal uses, and also as a test bed for ideas for what a smaller, simpler cloud might look like. My personal use case involves a relatively small environment which echos what we now think of as edge compute — less than 10 RU of machines with a minimum of orchestration and management overhead.

At the time that I was thinking about these things, the Australian bushfires and COVID-19 came along, and presented me with a lot more spare time than I had expected to have. While I’m still blessed to be employed, all of my social activities have been cancelled, so I find myself at home at a loose end on weekends and evenings a lot more than before.

Thus Shaken Fist was born — named for a Simpson’s meme, Shaken Fist is a deliberately small and highly opinionated cloud implementation aimed at working well in small deployments such as homes, labs, edge compute locations, deployed systems, and so forth.

I’d taken a bit of trouble with each feature in Shaken Fist to think through what the simplest and highest value way of doing something is. For example, instances always get a config drive and there is no metadata server. There is also only one supported type of virtual networking, and one supported hypervisor. That said, this means Shaken Fist is less than 5,000 lines of code, and small enough that new things can be implemented very quickly by a single middle aged developer.

I think Shaken Fist is useful to others. Its apache2 licensed, and available on github if you’re interested.

## "I'd like to subscribe to your newsletter"

First off, we don't have a newsletter. That said, updates will be announced here as they happen. What we do have is useful links:

* [The deploy repository](http://github.com/shakenfist/deploy) is the installer code for Shaken Fist, and probably a good place to start, especially if you just want to use Shaken Fist and not contribute code to it.

* [The Shaken Fist repository](http://github.com/shakenfist/shakenfist) is where the code for the server is.

* There are a series of client repositories as well:
    * [The python client repostiory](http://github.com/shakenfist/client-python) contains the python API client, as well as the command line client that users and shell scripts use to interact with Shaken Fist.
    * [The golang client repository](http://github.com/shakenfist/client-go) contains... wait for it... the golang client library. This is used by the terraform provider.
    * [The javascript client repository](http://github.com/shakenfist/client-js) contains... wait for it... a javascript client library. It should be noted that this client is currently incomplete.

* [The terraform provider repository](http://github.com/shakenfist/terraform-provider-shakenfist) has a Terraform provider for Shaken Fist which uses the golang client.

* [The ansible modules repository](http://github.com/shakenfist/ansible-modules) contains a few simple modules for using Shaken Fist in ansible roles and plays.

We also have a few "more internal" respositories:

* [The loadtest repository](http://github.com/shakenfist/loadtest) contains a simpler load tester we use for validating releases.

* [The reproducables repository](http://github.com/shakenfist/reproducables) contains simple test cases for reproducing strange behaviours we have seen while developing Shaken Fist.

## New user guides

We need more of these. For now, we have the following:

* [An installation guide](installation.md)

## Release changelogs

Version v0.2 was when we started taking releases and upgrades seriously. Thus, its also when we started writing changelogs:

* [v0.2 series](changelog/changelog_v02.md)
* [v0.3 series](changelog/changelog_v03.md)

## API documentation

There is some limited API documentation, its definitely a known gap.

* [The power states an instance can be in](power_states.md)

## Documentation for developers of Shaken Fist
* We have [release documentation](development/release_process.md). We found this mildly surprising too.
* Everyone is confused by the networking, so we wrote some [networking documentation](networking/overview.md). Hopefully it helps.
