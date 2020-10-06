---
title: Manifesto
---
# Shaken Fist Manifesto

This document attempts to list Shaken Fist's defining features, give guidance on what type of features should be added to the project, how they should be implemented and how we work together.

## Shaken Fist Defining Characteristics

* Shaken Fist is smaller, simpler cloud.
* It is designed for relatively small environments with minimum management overhead.
* Its features are highly opinionated. This means that the maintainers have chosen the best (in their opinion) features to support.
* Opinionated features do not handle every single possible use case. This reduces the code base size thus increasing long-term maintainability.
* The code base is understandable in its entirety by a single developer.
* A Shaken Fist cluster does not need a team of engineers to install or operate.
* A Shaken Fist cluster should be simple to set up. We define 'simple' as "a person with no knowledge of the project can build a reasonable cluster in an evening".


## Project Goals

* Allow simple management of virtual machine instances without complexity.
* Support networking between those machines and also facilitate access to external networks.
* Avoid re-inventing the wheel (utilise other open source projects when appropriate).


## Feature Guidelines

* Features should be deliberately limited in the options available.
* The goal of limiting options is to reduce code complexity. If the option does not add significant code complexity then it should added.
* The supported features and the options of those features should aim to cover the majority of use cases.
* When a feature limits the available options, it should do so in a way that does not overly restrict a project fork from adding that option.
* New code should conform to the conventions of the existing code base and written to be easily understood.
* New code should have new tests (please).


## Significant Opinionated Design Decisions

* The only supported hypervisor is KVM managed by libvirt.
* Virtual networking is only implemented via VXLAN meshes.
* Single machine clusters should always be possible.
* Only the current Ubuntu LTS version is supported by the main project (forks to support other operating systems are encouraged).


## Project Interaction Guidelines

* Always polite
* Always generous
* Being opinionated is encouraged (but gently)
* Updating the documentation is just as important as the code change itself
* Developers who write tests are the most highly prized of all the developers
