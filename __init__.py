"""The Bond integration."""
from asyncio import TimeoutError as AsyncIOTimeoutError

from aiohttp import ClientError, ClientTimeout
from bond_api import Bond, BPUPSubscriptions, start_bpup

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import SLOW_UPDATE_WARNING

from .const import BPUP_STOP, BPUP_SUBS, BRIDGE_MAKE, DOMAIN, HUB
from .utils import BondHub

PLATFORMS = ["cover", "fan", "light", "switch"]
_API_TIMEOUT = SLOW_UPDATE_WARNING - 1
_STOP_CANCEL = "stop_cancel"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bond from a config entry."""
    host = entry.data[CONF_HOST]
    token = entry.data[CONF_ACCESS_TOKEN]
    config_entry_id = entry.entry_id

    bond = Bond(
        host=host,
        token=token,
        timeout=ClientTimeout(total=_API_TIMEOUT),
        session=async_get_clientsession(hass),
    )
    hub = BondHub(bond)
    try:
        await hub.setup()
    except (ClientError, AsyncIOTimeoutError, OSError) as error:
        raise ConfigEntryNotReady from error

    bpup_subs = BPUPSubscriptions()
    stop_bpup = await start_bpup(host, bpup_subs)

    @callback
    def _async_stop_event(event: Event) -> None:
        stop_bpup()

    stop_event_cancel = hass.bus.async_listen(
        EVENT_HOMEASSISTANT_STOP, _async_stop_event
    )
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        HUB: hub,
        BPUP_SUBS: bpup_subs,
        BPUP_STOP: stop_bpup,
        _STOP_CANCEL: stop_event_cancel,
    }

    if not entry.unique_id:
        hass.config_entries.async_update_entry(entry, unique_id=hub.bond_id)

    assert hub.bond_id is not None
    hub_name = hub.name or hub.bond_id
    device_registry = await dr.async_get_registry(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={(DOMAIN, hub.bond_id)},
        manufacturer=BRIDGE_MAKE,
        name=hub_name,
        model=hub.target,
        sw_version=hub.fw_ver,
        suggested_area=hub.location,
    )

    _async_remove_old_device_identifiers(config_entry_id, device_registry, hub)

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data[DOMAIN][entry.entry_id]
    data[_STOP_CANCEL]()
    if BPUP_STOP in data:
        data[BPUP_STOP]()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


@callback
def _async_remove_old_device_identifiers(
    config_entry_id: str, device_registry: dr.DeviceRegistry, hub: BondHub
) -> None:
    """Remove the non-unique device registry entries."""
    for device in hub.devices:
        dev = device_registry.async_get_device(identifiers={(DOMAIN, device.device_id)})
        if dev is None:
            continue
        if config_entry_id in dev.config_entries:
            device_registry.async_remove_device(dev.id)
