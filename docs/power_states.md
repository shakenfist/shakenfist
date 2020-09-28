Instance power states
=====================

Shaken Fist version 0.2.1 introduced power states for instances. Before this, you could power on or off an instance, or pause it, but you couldn't tell what power state the instance was actually in. That was pretty confusing and was therefore treated as a bug.

The following power states are implemented:

* **on**: the instance is running
* **off**: the instance is not running
* **paused**: the instance is paused
* **crashed**: the instance is crashed according to the hypervisor. Instances in this power state will also be in an instance state of "error".

There are additionally a set of "transition states" which are used to indicate that you have requested a change of state that might not yet have completed. These are:

* transition-to-on
* transition-to-off
* transition-to-paused

We're hoping to not have to implement a transition-to-crashed state, but you never know.
