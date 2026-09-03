"""Parsers for the three free, machine-readable government sanctions feeds:
OFAC's Specially Designated Nationals list, the UN Security Council's
Consolidated List, and the EU's Financial Sanctions Files (FSF) export. Each
publishes its own XML schema; every parser here turns its shape into the
same :class:`~crr.screening.models.WatchlistRecord`, so nothing downstream —
matching, storage, the UI — needs to know which source a record came from.

Parsed with :mod:`defusedxml` rather than the standard library's
``xml.etree.ElementTree`` directly: these files are fetched over the network
(see :mod:`crr.screening.ingest`), and a hostile or corrupted response should
fail loudly rather than get a chance at entity-expansion or external-entity
tricks. ``defusedxml.ElementTree`` has the exact same API, so nothing else
about these parsers is unusual ElementTree code.

Schema note: none of the three publishers version their schema with the
rigor an API would — a field renamed or restructured upstream will make the
matching ``findall``/``get`` below quietly return nothing for that field
rather than raise. ``scripts/refresh_watchlists.py`` prints a per-source
parsed-row count for exactly this reason: a real deployment's first check
after wiring in a fresh source is "does this count look like the real list
size", not "did parsing raise."
"""

from __future__ import annotations

from collections.abc import Iterable
from xml.etree.ElementTree import Element  # noqa: S405 — for type hints only; parsing itself uses defusedxml below

from defusedxml import ElementTree as DefusedET

from crr.screening.models import WatchlistRecord

#: Source keys this module (and crr.screening as a whole) understands.
SOURCES = ("ofac", "un", "eu")


def _local(tag: str) -> str:
    """Strip a namespace prefix — ``{uri}tag`` -> ``tag`` — so parsing does
    not hardcode a namespace URI that a schema-version bump would break."""
    return tag.rsplit("}", 1)[-1]


def _text(elem: Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _findall_local(root: Element, tag: str) -> Iterable[Element]:
    """``root.iter(tag)`` ignoring whatever namespace is in scope."""
    return (e for e in root.iter() if _local(e.tag) == tag)


def _children_local(parent: Element, tag: str) -> Iterable[Element]:
    return (e for e in parent if _local(e.tag) == tag)


def _child_text(parent: Element, tag: str) -> str:
    return next((_text(e) for e in _children_local(parent, tag)), "")


# --------------------------------------------------------------------------
# OFAC — Specially Designated Nationals (SDN) list
# https://www.treasury.gov/ofac/downloads/sdn.xml
#
# <sdnList xmlns="...">
#   <sdnEntry>
#     <uid>36</uid> <firstName>...</firstName> <lastName>...</lastName>
#     <sdnType>Individual</sdnType>
#     <programList><program>SDGT</program>...</programList>
#     <akaList><aka><firstName/><lastName/></aka>...</akaList>
#     <dateOfBirthList><dateOfBirthItem><dateOfBirth>01 Jan 1970</dateOfBirth></dateOfBirthItem>...</dateOfBirthList>
#     <addressList><address><country>...</country></address>...</addressList>
#     <remarks>...</remarks>
#   </sdnEntry>
# </sdnList>
#
# Namespaced (the URI carries a schema-version suffix Treasury has changed
# before), which is exactly why every lookup here goes through the
# namespace-stripping helpers above instead of a literal "{uri}tag".
# --------------------------------------------------------------------------


def parse_ofac_sdn(content: bytes) -> list[WatchlistRecord]:
    root = DefusedET.fromstring(content)
    records = []
    for entry in _findall_local(root, "sdnEntry"):
        first = _child_text(entry, "firstName")
        last = _child_text(entry, "lastName")
        name = " ".join(p for p in (first, last) if p) or last or first
        if not name:
            continue  # an entry with neither name field is not screenable

        aliases: list[str] = []
        for aka_list in _children_local(entry, "akaList"):
            for aka in _children_local(aka_list, "aka"):
                aka_name = " ".join(
                    p for p in (_child_text(aka, "firstName"), _child_text(aka, "lastName")) if p
                )
                if aka_name:
                    aliases.append(aka_name)

        dobs: list[str] = []
        for dob_list in _children_local(entry, "dateOfBirthList"):
            for item in _children_local(dob_list, "dateOfBirthItem"):
                dob = _child_text(item, "dateOfBirth")
                if dob:
                    dobs.append(dob)

        countries: list[str] = []
        for addr_list in _children_local(entry, "addressList"):
            for address in _children_local(addr_list, "address"):
                country = _child_text(address, "country")
                if country:
                    countries.append(country)

        programs = [
            _text(p) for p_list in _children_local(entry, "programList") for p in _children_local(p_list, "program")
        ]

        records.append(WatchlistRecord(
            source="ofac",
            source_id=_child_text(entry, "uid") or name,
            name=name,
            category="sanctions",
            aliases=tuple(dict.fromkeys(aliases)),
            dates_of_birth=tuple(dict.fromkeys(dobs)),
            countries=tuple(dict.fromkeys(countries)),
            program=", ".join(dict.fromkeys(programs)),
            remarks=_child_text(entry, "remarks"),
        ))
    return records


# --------------------------------------------------------------------------
# UN Security Council Consolidated List
# https://scsanctions.un.org/resources/xml/en/consolidated.xml
#
# <CONSOLIDATED_LIST>
#   <INDIVIDUALS><INDIVIDUAL>
#     <FIRST_NAME/> <SECOND_NAME/> <THIRD_NAME/>
#     <UN_LIST_TYPE/> <REFERENCE_NUMBER/>
#     <INDIVIDUAL_ALIAS><ALIAS_NAME/></INDIVIDUAL_ALIAS>...
#     <INDIVIDUAL_DATE_OF_BIRTH><DATE/></INDIVIDUAL_DATE_OF_BIRTH>...
#     <NATIONALITY><VALUE/></NATIONALITY>...
#     <COMMENTS1/>
#   </INDIVIDUAL></INDIVIDUALS>
#   <ENTITIES><ENTITY>...</ENTITY></ENTITIES>
# </CONSOLIDATED_LIST>
#
# No default namespace on this one, but the shared helpers cost nothing to
# reuse and keep both parsers reading the same way.
# --------------------------------------------------------------------------


def parse_un_consolidated(content: bytes) -> list[WatchlistRecord]:
    root = DefusedET.fromstring(content)
    records = []
    for section, tag in (("INDIVIDUALS", "INDIVIDUAL"), ("ENTITIES", "ENTITY")):
        for group in _findall_local(root, section):
            for entry in _children_local(group, tag):
                name_parts = [
                    _child_text(entry, part) for part in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")
                ]
                name = " ".join(p for p in name_parts if p)
                if not name:
                    continue

                aliases = [
                    _child_text(aka, "ALIAS_NAME")
                    for aka in _findall_local(entry, "INDIVIDUAL_ALIAS" if tag == "INDIVIDUAL" else "ENTITY_ALIAS")
                ]
                aliases = [a for a in aliases if a]

                dob_tag = "INDIVIDUAL_DATE_OF_BIRTH"
                dobs = [
                    _child_text(item, "DATE") or _child_text(item, "YEAR")
                    for item in _findall_local(entry, dob_tag)
                ]
                dobs = [d for d in dobs if d]

                countries = [_child_text(nat, "VALUE") for nat in _findall_local(entry, "NATIONALITY")]
                countries = [c for c in countries if c]

                records.append(WatchlistRecord(
                    source="un",
                    source_id=_child_text(entry, "REFERENCE_NUMBER") or _child_text(entry, "DATAID") or name,
                    name=name,
                    category="sanctions",
                    aliases=tuple(dict.fromkeys(aliases)),
                    dates_of_birth=tuple(dict.fromkeys(dobs)),
                    countries=tuple(dict.fromkeys(countries)),
                    program=_child_text(entry, "UN_LIST_TYPE"),
                    remarks=_child_text(entry, "COMMENTS1"),
                ))
    return records


# --------------------------------------------------------------------------
# EU Financial Sanctions Files (FSF) consolidated list
# https://webgate.ec.europa.eu/fsd/fsf (requires a per-session download
# token minted through the EU Sanctions Map UI — see ingest.py's docstring).
#
# Attribute-heavy, unlike the two text-element-heavy feeds above:
#
# <export>
#   <sanctionEntity euReferenceNumber="EU.123.45" ...>
#     <subjectType code="P"/>  <!-- P=person, E=entity -->
#     <regulation programme="..." .../>
#     <remark>...</remark>
#     <nameAlias wholeName="..." firstName="..." lastName="..." strong="true"/>
#     <birthdate birthdate="1970-01-01" .../>
#     <citizenship countryIso2Code="XX" .../>
#   </sanctionEntity>
# </export>
# --------------------------------------------------------------------------


def parse_eu_fsf(content: bytes) -> list[WatchlistRecord]:
    root = DefusedET.fromstring(content)
    records = []
    for entity in _findall_local(root, "sanctionEntity"):
        names: list[str] = []
        for alias in _findall_local(entity, "nameAlias"):
            whole = alias.get("wholeName", "").strip()
            if not whole:
                whole = " ".join(
                    p for p in (alias.get("firstName", ""), alias.get("lastName", "")) if p.strip()
                ).strip()
            if whole:
                # strong="true" marks the primary/legal name; everything else
                # (transliterations, spelling variants) is an alias.
                (names.insert(0, whole) if alias.get("strong") == "true" else names.append(whole))
        if not names:
            continue
        primary, aliases = names[0], names[1:]

        dobs = [
            bd.get("birthdate", "").strip()
            for bd in _findall_local(entity, "birthdate")
        ]
        dobs = [d for d in dobs if d]

        countries = [
            c.get("countryIso2Code", "").strip()
            for c in _findall_local(entity, "citizenship")
        ]
        countries = [c for c in countries if c]

        programs = [r.get("programme", "").strip() for r in _findall_local(entity, "regulation")]
        programs = [p for p in programs if p]

        subject_type = next((s.get("code", "") for s in _findall_local(entity, "subjectType")), "P")

        records.append(WatchlistRecord(
            source="eu",
            source_id=entity.get("euReferenceNumber") or entity.get("logicalId") or primary,
            name=primary,
            category="sanctions",
            aliases=tuple(dict.fromkeys(aliases)),
            dates_of_birth=tuple(dict.fromkeys(dobs)),
            countries=tuple(dict.fromkeys(countries)),
            program=", ".join(dict.fromkeys(programs)),
            remarks=_child_text(entity, "remark") if subject_type else "",
        ))
    return records


PARSERS = {
    "ofac": parse_ofac_sdn,
    "un": parse_un_consolidated,
    "eu": parse_eu_fsf,
}


def parse_source(source: str, content: bytes) -> list[WatchlistRecord]:
    if source not in PARSERS:
        raise ValueError(f"unknown watchlist source {source!r} — expected one of {SOURCES}")
    return PARSERS[source](content)
