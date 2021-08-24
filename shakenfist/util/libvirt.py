import importlib


LIBVIRT = None


def get_libvirt():
    global LIBVIRT

    if not LIBVIRT:
        LIBVIRT = importlib.import_module('libvirt')

    return LIBVIRT


def extract_power_state(libvirt, domain):
    state, _ = domain.state()
    if state == libvirt.VIR_DOMAIN_SHUTOFF:
        return 'off'

    if state == libvirt.VIR_DOMAIN_CRASHED:
        return 'crashed'

    if state in [libvirt.VIR_DOMAIN_PAUSED,
                 libvirt.VIR_DOMAIN_PMSUSPENDED]:
        return 'paused'

    # Covers all "running states": BLOCKED, NOSTATE,
    # RUNNING, SHUTDOWN
    return 'on'
