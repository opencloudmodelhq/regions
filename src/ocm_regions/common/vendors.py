# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Display names for the vendors in the dataset."""

from typing import Final

VENDOR_NAMES: Final[dict[str, str]] = {
    "alibaba": "Alibaba",
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "Google Cloud",
    "oracle": "Oracle",
    "ibm": "IBM",
    "tencent": "Tencent",
    "huawei": "Huawei",
    "ovhcloud": "OVHcloud",
    "akamai": "Akamai",
    "hetzner": "Hetzner",
    "vultr": "Vultr",
    "digitalocean": "DigitalOcean",
}


def vendor_name(provider: str) -> str:
    """The vendor's display name for a provider ID, or the ID itself when it is unknown.

    Args:
        provider: Vendor identifier.

    Returns:
        The display name.
    """
    return VENDOR_NAMES.get(provider, provider)
