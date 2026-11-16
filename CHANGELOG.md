# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A change to the published data is at least a minor release.

## [Unreleased]

### Added

- `OCM_REGIONS_DNS` chooses a DNS-over-HTTPS resolver (`cloudflare`, `google` or an `https` address) for the public-address check, or `proxy` to leave it to the proxy.
- The workflow policy denies an App token that does not state its permissions.

### Changed

- A longer abstract in `CITATION.cff`, which the archive record shows as its description.
- Pull requests are merged by squash, so every commit on `main` is signed.

### Fixed

- A request sent through a proxy no longer looks the host name up on the local machine. A run through a proxy now stops until `OCM_REGIONS_DNS` is set.
- An address written in a legacy form, such as `127.1`, is checked without a lookup; `localhost` is refused on every route.
- The settings audit asks for an App token limited to reading administration settings and secret names.

## [1.0.0] - 2026-11-14

### Added

- OpenCloudModel Regions: 380 regions of 13 vendors mapped to places and to IATA, UN/LOCODE and CLLI codes.
- An evidence grade, a precision and evidence records for every region.
- Three naming tables: nearest airport, major hub and nearby hub.
- The pipeline that rebuilds every output from the published document list.
- The paper source and its generated tables and figures.
