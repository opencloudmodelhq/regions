PY ?= python3
MODEL ?= chain-qwen3.8
RUN = PYTHONPATH=src $(PY) -m ocm_regions.cli

-include .env
export OCM_REGIONS_USER_AGENT OCM_REGIONS_FROM OCM_REGIONS_REQUIRE_PROXY OCM_REGIONS_DNS HTTPS_PROXY HTTP_PROXY NO_PROXY

.PHONY: heldout heldout-memory quote-review method-stats provenance validate handcheck-sheets handcheck-passages label rivals repeat document-types changes scripted rag joins chain archive-missing area-risk mutation  extract-methods sensitivity experiment materials snapshots extract llm-model llm-matrix llm-repeat llm-up compare all fetch refresh regions place evidence grade codes calls report build caps test lint verify paper arxiv check typecheck licences citation external

all: fetch regions place build caps test

experiment: fetch regions place joins extract scripted evidence verify build provenance caps external heldout quote-review sensitivity mutation area-risk extract-methods compare method-stats paper validate test

chain: fetch regions document-types rag label rivals repeat heldout-memory place joins extract scripted evidence verify build provenance caps external heldout quote-review sensitivity mutation area-risk extract-methods compare method-stats paper changes validate test

fetch:
	$(RUN) fetch

refresh:
	$(RUN) fetch --force

regions:
	$(RUN) regions

place:
	$(RUN) place

llm-up:
	docker desktop enable model-runner --tcp=12434
	docker model pull ai/qwen3.8:27b-q8_0
	docker model pull ai/qwen3.6:35b-a3b-q8_0
	docker model pull ai/glm-4.7-flash:q8_0
	docker model pull ai/qwen3-embedding:4b-q8_0
	docker model pull ai/qwen3-reranker:4B

compare:
	$(RUN) compare --model $(MODEL)
method-stats:
	$(RUN) method-stats

snapshots:
	$(RUN) snapshots

sensitivity:
	$(RUN) sensitivity

materials:
	$(RUN) materials

extract:
	$(RUN) extract

extract-methods: extract
	$(RUN) extract-methods

mutation:
	$(RUN) mutation

area-risk:
	$(RUN) area-risk

external:
	$(RUN) external

heldout:
	$(RUN) heldout

heldout-memory:
	$(RUN) heldout-memory --model $(MODEL)

quote-review:
	$(RUN) quote-review

archive-missing:
	$(RUN) archive-missing

llm-model:
	for method in label memory rag; do $(RUN) llm $$method --model $(MODEL) || exit 1; done
	$(RUN) compare --model $(MODEL)

llm-matrix:
	for model in $$($(PY) -c "import json; print(' '.join(json.load(open('config/llm_models.json'))))"); do $(MAKE) llm-model MODEL=$$model || exit 1; done

llm-repeat:
	$(RUN) llm-repeat --model $(MODEL) --method $(or $(METHOD),label)

joins:
	$(RUN) joins

document-types:
	$(RUN) document-types --model $(MODEL)

rag:
	docker model unload --all
	$(RUN) llm rag --model $(MODEL)
	$(RUN) llm memory --model $(MODEL)

handcheck-sheets:
	$(RUN) handcheck-sheets

handcheck-passages:
	$(RUN) handcheck-passages --sheet $(SHEET)

label:
	$(RUN) llm label --model $(MODEL)

rivals:
	for model in $$($(PY) -c "import json; print(' '.join(m for m in json.load(open('config/llm_models.json')) if m != '$(MODEL)'))"); do docker model unload --all; $(RUN) llm rag --model $$model || exit 1; done

repeat:
	docker model unload --all
	$(RUN) llm-repeat --model $(MODEL) --method rag

scripted:
	$(RUN) scripted --model $(MODEL)

changes:
	$(RUN) changes

evidence:
	$(RUN) evidence

grade:
	$(RUN) grade

codes:
	$(RUN) codes

calls:
	$(RUN) calls

report:
	$(RUN) report

build: report

provenance:
	$(RUN) provenance

validate:
	uvx cffconvert@2.0.0 --validate -i CITATION.cff
	uvx --from roc-validator==0.13.1 rocrate-validator validate -p ro-crate-1.1 -l recommended --no-paging -f text .
	uvx --from "reuse[charset-normalizer]==6.3.0" reuse lint
	$(RUN) provenance-check

caps:
	$(RUN) caps

test:
	$(PY) -m pytest -q

lint:
	ruff check src tests
	ruff format --check src tests

typecheck:
	pyright

licences:
	reuse lint

citation:
	cffconvert --validate

check: lint typecheck licences citation test

verify:
	$(RUN) verify

paper: place sensitivity caps external compare
	cd paper && tectonic --keep-logs --keep-intermediates main.tex

arxiv: paper
	cd paper && tar czf arxiv-source.tar.gz main.tex main.bbl refs.bib generated figures
