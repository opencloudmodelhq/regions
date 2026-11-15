# OpenCloudModel Regions

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24215448.svg)](https://doi.org/10.5281/zenodo.24215448)

An evidence-linked dataset of cloud region locations and codes. It maps the regions published by AWS, Azure, Google Cloud, Alibaba Cloud, Oracle Cloud, Tencent Cloud, Huawei Cloud, IBM Cloud, Hetzner, Vultr, DigitalOcean, OVHcloud and Akamai to a place and to IATA, UN/LOCODE and inferred CLLI codes, rebuilt by script from a published list of document links. Of 380 regions in scope, 379 have a place, and the one without is reported as a gap.

A code is a label. It does not prove where a data centre is. Each region carries an evidence grade and a stated precision, so a reader can see how far to trust it, and every gap or doubt is published as a call.

After the URL list, every step is code or a local model at temperature 0, with a fixed seed and a frozen prompt whose hash is recorded. Rule parameters are stated. The choices people still make are the document list, the scope exclusions, the rule parameters, and the prompts and rules themselves.

The accompanying paper is `paper/main.pdf`. This file is the guide: how to run it, how a place is chosen, the rule parameters, the files, and the known limits.

## Run it

Python 3.12 or newer with `numpy`, and `pdftotext` for PDFs. Tests need `pytest`, `rdflib`, `prov` and `pyshacl`. `make paper` needs `tectonic`. `make validate` needs `uv`, which runs pinned upstream validators in their own environments.

```sh
make experiment   # every stage, using the recorded model answers; no model needed
make chain        # every stage, asking the local models again
```

| Stage | What it does |
|---|---|
| `make fetch` | Installs the corpus: downloads each listed dataset and document, checks hashes, uses archive copies and hand downloads, lists what is missing |
| `make regions` | Parses every published region |
| `make document-types` | Asks a local model who published each document and what kind it is; needs `make llm-up` |
| `make rag` | Embeds, retrieves, reranks and asks a local model, then asks it with no documents; needs `make llm-up` |
| `make place` | Places regions by rule and scores them against the reference |
| `make provenance` | Writes `output/provenance.jsonld`, the W3C PROV-O record of every placement |
| `make validate` | Checks `CITATION.cff` (cffconvert), the RO-Crate (rocrate-validator, RO-Crate 1.1), licences (reuse lint) and the PROV record (W3C prov library and SHACL shapes) |
| `make joins` | Joins audit certificate codes to government records |
| `make extract` | Finds lines naming a region and a place |
| `make scripted` | Applies the quote rules to the search and model output |
| `make evidence` | Attaches every record to its region |
| `make verify` | Checks every quote against its source (network) |
| `make build` | Runs `grade`, `codes`, `calls` and `report`: the nearest-airport, major-hub and nearby-hub tables, the report, the explorer and the paper's numbers |
| `make caps` | Tests every hub distance cap and draws the figures |
| `make external` | Compares places with the Green Software Foundation's dataset, and codes with the codes Cloudflare chose for its own sites |
| `make heldout-memory` | Asks the local model where Zenlayer's regions are from their identifiers alone; needs `make llm-up` |
| `make heldout` | Scores the frozen placement and code rules on Fastly and Zenlayer, vendors that had no part in building them |
| `make quote-review` | Scores the author's verdicts on every quoted evidence record against the stance each record carries |
| `make sensitivity`, `mutation`, `area-risk`, `extract-methods`, `compare` | Checks on the rules, the quote check, the codes and the models |
| `make paper` | Builds `paper/main.pdf` |
| `make changes` | Lists regions whose place, code or grade moved since the last commit |
| `make test` | Runs the test suite |

`make refresh` downloads sources again and moves their pinned hashes; an ordinary `make fetch` keeps them.

Documents a site refuses to serve to a script can be saved by hand into `downloads/` (or `OCM_REGIONS_DOWNLOADS`); `make fetch` installs them by hash.

### Identify your own client

Requests go out as `ocm-regions-harness/0.1` unless you say otherwise. The harness reads each site's `robots.txt` under that name and leaves unread what the file disallows; those documents are reported as `robots-disallowed` and are not taken from an archive. A person may open such a page in a browser and save it into `downloads/`. Five listed sources on sites that disallow robots were fetched before this check was added and are kept: `download.geonames.org`, `api.vultr.com`, `frs-public.epa.gov`, `mslocalblocks.wpenginepowered.com` and one `aws.amazon.com` page. If you run this at any scale, name your own client and contact so a site owner can reach you: copy `.env.example` to `.env` (git ignores it) and set `OCM_REGIONS_USER_AGENT` and `OCM_REGIONS_FROM`. Do not reuse another operator's name or address. The project's own runs go out as `OpenCloudModelBot`, described at <https://opencloudmodel.org/crawler> with the addresses they come from; that name is the project's, so do not send it yourself. The standard `HTTPS_PROXY` and `HTTP_PROXY` variables route requests through a proxy; with `OCM_REGIONS_REQUIRE_PROXY=1` the harness refuses to send anything without one.

Before a document is fetched, the harness checks that its host is on the public internet. An address written out in the URL, and `localhost`, are checked on every route. Without a proxy, the harness checks the addresses this machine's resolver gives, because those are the ones it connects to. Through a proxy, it never looks a name up on this machine, so the name does not reach your local resolver; you must then say in `OCM_REGIONS_DNS` who resolves it, and the harness refuses to run until you do. Set it to `cloudflare`, `google` or the `https` address of another DNS-over-HTTPS resolver that answers in JSON, and the harness asks that resolver through the proxy and refuses a name with a private address or with none. Set it to `proxy` to state that your proxy resolves names and itself refuses private addresses; the harness then checks nothing for a name. A resolver's answer is a second check, not a guarantee: the proxy resolves the name again when it connects, so its own rules must still refuse private addresses. Without a proxy, a resolver you name is asked as well as this machine's. The resolver you name sees every host the harness reads.

## How a place is chosen

Code applies one order to every region (`src/ocm_regions/place/placement.py`):

1. The vendor's label or region ID (`label`).
2. A place a local model read in a document, where code found the quote and the document is not a listing or a network page (`document`). Over a feed, the quote must also name the region.
3. A town a vendor sentence locates the label's place in, when the label names a place the town list lacks (`document`).
4. The vendor's IP feed (`feed`).
5. The capital, when a label names only a country (`capital`).
6. The place the model recalls with no document (`memory`).
7. The capital of a country at the end of a label (`capital`).

The `place_basis` column of `output/regions.csv` says what set each place (`label`, `document`, `feed`, `memory`, `capital` or `none`), and `cross_check` says whether a second signal agrees or conflicts. A hub is the airport with the highest OpenTravelData page rank in the country within 200 km.

## Add to the corpus

Add a row with a provider and a URL to `sources/documents.csv`, run `make materials`, then `make chain`. The document's kind comes from `make document-types`, the embedding index follows a hash of the corpus, a new city gets a place from GeoNames with its hub by rule, and `make changes` lists what moved. A new vendor needs a parser for its region list and a row in `config/operators.csv`; no code names a region.

## Outputs

| File | Contents |
|---|---|
| `output/regions.csv` | One row per region: place, how it was chosen, grade, precision, all codes, reasoning and evidence links |
| `output/codes_nearest_airport.csv` | Nearest airport: nearest scheduled airport |
| `output/codes_major_hub.csv` | Major hub: best-connected airport by page rank within 200 km; a hub must be in the region's country |
| `output/codes_nearby_hub.csv` | Nearby hub: the major hub within the cap the data selects (90 km), otherwise the nearest airport |
| `output/evidence.csv` | Each source claim, with URL, quote and stance |
| `output/joins.jsonl`, `output/scripted_evidence.jsonl` | Evidence read from the corpus by code |
| `output/calls.csv` | Every gap and doubt, with severity |
| `output/weak_evidence.csv` | Regions to distrust first |
| `output/area_risk.csv` | Weak regions, sorted by whether a rival city would change the code |
| `output/cap_sweep.csv`, `output/cap_summary.json` | Every candidate cap, its measures and stability tests |
| `output/external_places.csv`, `output/external_codes.csv`, `output/external_summary.json` | Distance from each place to an outside dataset's point, and each table's code against an operator's own |
| `output/candidate_regions.csv` | Region IDs in vendors' IP ranges but not their lists; not counted as regions |
| `output/llm/<model>/` | Recorded document types and model answers, their manifest with prompt hashes, and scores |
| `output/report.md`, `site/index.html` | Issues report and single-file explorer |
| `output/provenance.jsonld` | W3C PROV-O record: placement ← evidence ← document, and the step, code digest, model build and prompt behind each (`make provenance`) |

`ro-crate-metadata.json` describes the dataset in RO-Crate 1.1, and `CITATION.cff` gives the citation GitHub shows.

## Evidence grades

Strongest first. A region takes the best grade its evidence supports.

| Grade | Meaning |
|---|---|
| `vendor-stated` | The vendor's region list names the place |
| `vendor-document` | A vendor document supports the place for this region |
| `audit-document` | An audit or certification document names the place |
| `public-record` | An audit list ties the region to a facility code, and a government record puts the same operator and code in the area |
| `measured` | Reserved for network measurement; none has been run |
| `vendor-feed` | The vendor's IP location feed places the region's addresses here; supports an area |
| `reported` | Press, government or directory source supports it |
| `network-presence` | Only a PeeringDB entry points there, which shows peering, not hosting |
| `partial` | A source shows the vendor's presence without naming the region, a record failed the quote check, or only the model's memory gives the place |
| `assumed` | No source checked supports it |

`precision` is `city` when the vendor's label names the city and `metro` otherwise. `hub_class` says whether the nearby hub is an airport OurAirports classes as `large` or `medium`, so a reader who wants only large airports can filter. `area_basis` says how the code was reached: `exact-city`, `metro-hub` (no clear city or weak evidence, so the metro hub within the cap) or `nearest-hub-too-far` (hub beyond the cap, so the nearest airport). The cap comes from the selection rule in `src/ocm_regions/analysis/capsweep.py`.

## Inputs

| Path | Contents |
|---|---|
| `sources/documents.csv`, `sources/datasets.csv` | The corpus: every document and dataset by URL, hash and archive copy |
| `prompts/` | The frozen model prompts |
| `config/llm_config.json`, `config/llm_models.json` | Model settings: temperature 0, seed 42, and the model names |
| `config/operators.csv` | The company name of each vendor |
| `evaluation/reference_placements.csv`, `evaluation/overturned_placements.csv` | Earlier placements, used only for scoring; they reach no table |
| `evaluation/quote_review.csv` | The author's verdict on each quoted evidence record, after opening its source; used only for scoring |
| `registers/codes_table_*.csv` | Append-only code registers; a code, once assigned, is kept while its airport stays the same |

## Rule parameters

Every number a rule uses is stated here and set in one place in the code.

| Parameter | Value | Code |
|---|---|---|
| Signals agree within | 50 km | `place/placer.py` `AGREE_KM` |
| Smallest city named anywhere in a label | 20,000 people | `place/placer.py` `MIN_POPULATION` |
| Smallest city sharing a state's or country's name | 100,000 people | `place/placer.py` `AREA_NAMESAKE_POPULATION` |
| Top feed city's share of addresses | At least 0.5 | `place/feeds.py` `MIN_SHARE` |
| Hub search radius | 200 km | `place/hubs.py` `RULE_KM` |
| Hub cap for the nearby hub and the area rule | Selected from the data by rule | `analysis/capsweep.py` `table_cap` |
| Radius for matching a quote, join, facility or UN/LOCODE to a placed city | 100 km | `common/geo.py` `MATCH_KM` |
| Shortest quote accepted as evidence | 20 characters | `evidence/scripted.py` `MIN_QUOTE_CHARS` |
| Vendor code conflict | Over 300 km | `tables/build.py` `CONFLICT_KM` |
| Model settings | Temperature 0, fixed seed, token limit | `config/llm_config.json` |
| Retrieval | 900-character passages, 150 overlap, 30 reranked, 6 read | `model/llm.py` constants |
| Text read to type a document | First 3,000 characters | `model/document_types.py` `TEXT_CHARS` |

What people still choose: the document list (`sources/`), the scope exclusions (`common/data.py`), these parameters, and the prompts and rules themselves.

## Source layout

`src/ocm_regions/` has one package per stage, and `cli.py` on top.

| Package | Holds | Make targets |
|---|---|---|
| `common/` | Shared types, input paths, loaders, distance | all |
| `fetch/` | Dataset and document download, hashes, archive copies | `fetch`, `materials`, `snapshots` |
| `regions/` | One parser per vendor's region list | `regions` |
| `place/` | The placement rules, feeds, hubs and the placement order | `place` |
| `model/` | Local model client, prompts, retrieval, document types | `document-types`, `rag`, `label` |
| `evidence/` | Quote search, quote rules, joins, the quote check | `extract`, `scripted`, `joins`, `verify` |
| `tables/` | Grades, codes, calls, registers and the report | `build` |
| `analysis/` | Cap sweep, sensitivity, mutation test, area risk, method comparison | `caps` to `method-stats` |
| `publish/` | Paper inputs, explorer page, PROV-O export and its check | `paper`, `provenance`, `validate` |

## Known limits

Method:

- **The document list is chosen.** A different search would find other documents. Only the steps after the list repeat.
- **Scores measure agreement, not truth.** The reference is an earlier placement built on the same vendor labels, and the rules were tuned against it.
- **The vendor signals are not independent.** Labels and feeds both come from the vendor, so a cross-check that `agrees` does not mean confirmed.
- **A quote check shows that words exist.** It does not show that they support the claim, and no person has audited a sample.
- **A document's kind is one model's reading.** It sets what the model may read, what can place a region and which grade a quote earns.
- **A join can rest on an expired list.** The certificate behind the Azure joins was valid until June 2025, and no code tests that date.
- **Live pages change.** A pin detects it; only a person's copy or an archive copy can restore a document.
- **Places use a city-centre coordinate.** Campuses can be far away, so distances are indicative.
- **The cap is one value from a range.** Any cap in the stable range gives the same codes.
- **No location measurement exists.** The `measured` grade is reserved and unused.

Places and codes:

- **The page rank hub can pick the smaller airport.** Page rank measures connections, not passengers, so Osaka takes ITM over KIX and Bangkok takes DMK over BKK.
- **Some regions rest on model memory.** They have `place_basis=memory`, no document and grade `partial`.
- **The label beats documents.** `ovhcloud HIL` stays at Seattle although OVHcloud's own document says Hillsboro; an `evidence-conflict` call flags it.
- **A capital is a coarse guess.** `alibaba na-south-1` takes Mexico City.
- **Two spellings make two places.** A place is keyed on its name, so `Queretaro` and `Santiago de Querétaro` are separate places at the same point. Their codes agree.
- **The UN/LOCODE match can take a namesake**, and the CLLI match accepts malformed values.

Running it:

- **`make fetch` exits 0 with files missing.** It lists them, and the chain continues on what it has.
- **`make rag` unloads every Docker model on the machine.**
- **`make chain` asks one model.** The other models' recorded answers follow a corpus change only after `make llm-matrix`.
- **A frozen prompt is frozen by convention.** Its hash is recorded; nothing refuses an edited prompt.
- **The served model is not checked** against the model ID recorded in the manifest.
- **A quote found by two methods is listed twice** in `output/evidence.csv`.

## Hand check

`make handcheck-sheets` writes two blank review sheets to `evaluation/`: one to check each sampled region against the corpus, one to search independently. Fill them from public sources only, at city level. The check is an evaluation; no stage of the chain reads it.

## Licence

Code (`src/`, `tests/`, `Makefile`, `prompts/`) is under the Apache License 2.0, in `LICENSE`. The dataset (`output/`, `sources/`, `config/`, `registers/`, `evaluation/`, `site/`) is under CC BY 4.0, in `LICENSE-DATA`. The paper (`paper/`) is not under either licence; all rights in it are reserved. The dataset draws on GeoNames and OpenTravelData (CC BY 4.0), OurAirports (public domain), PeeringDB and UN/LOCODE; their terms still apply to that content. Quotes from vendor and other documents are short quotations; they remain the property of their publishers and are not covered by the CC BY licence.

## Redistribution

`data/raw/` and `downloads/` are not committed. Vendor pages and PDFs stay under their owners' terms, so the harness downloads them again and the corpus lists their hashes. Sites are published at city level; a quote holding a street address is cut back to the city.

UN/LOCODE is licensed CC BY 4.0 by UNECE. Airport codes here come from OurAirports, not from IATA. For the full, authoritative code list and how to obtain it, see IATA's [Airport and Location Identifier Database](https://www.iata.org/en/publications/manuals/airline-airport-location-coding-databases/airport-location-identifier-database/). See also the paper's section on reuse terms.
