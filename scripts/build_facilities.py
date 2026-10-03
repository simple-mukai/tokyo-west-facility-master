#!/usr/bin/env python3
"""Build and validate the facility master from osmium GeoJSON Sequence."""

import argparse
import datetime
import hashlib
import json
import math
import os
import sys

SCHEMA_VERSION = 1
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE = "ODbL-1.0"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"

KEEP_TAG_KEYS = ["shop", "amenity", "highway", "name:ja", "name", "brand", "operator"]
CATEGORY_ORDER = ["supermarket", "convenience", "hospital", "pharmacy", "kindergarten", "school", "bus_stop"]
TYPE_ORDER = {"node": 0, "way": 1, "relation": 2}
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_AT_EQUATOR = 111.320


def load_area(path):
    with open(path, "r", encoding="utf-8") as f:
        area = json.load(f)
    b = area["candidateBbox"]
    if not (b["west"] < b["east"] and b["south"] < b["north"]):
        raise SystemExit("area.json: candidateBbox is invalid")
    return area


def classify(tags):
    shop = tags.get("shop")
    amenity = tags.get("amenity")
    if shop == "supermarket":
        return "supermarket"
    if shop == "convenience":
        return "convenience"
    if amenity == "hospital":
        return "hospital"
    if amenity == "pharmacy":
        return "pharmacy"
    if amenity in ("kindergarten", "childcare"):
        return "kindergarten"
    if amenity == "school":
        return "school"
    if tags.get("highway") == "bus_stop":
        return "bus_stop"
    return None


def iter_geojsonseq(path):
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip().lstrip("\x1e").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSON ({exc})")


def iter_positions(coords):
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        yield coords
        return
    for child in coords:
        yield from iter_positions(child)


def geometry_bbox(geometry):
    if not geometry:
        return None
    if geometry.get("type") == "GeometryCollection":
        boxes = [geometry_bbox(g) for g in geometry.get("geometries", [])]
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
    west = south = math.inf
    east = north = -math.inf
    found = False
    for pos in iter_positions(geometry.get("coordinates")):
        lon, lat = float(pos[0]), float(pos[1])
        west, east = min(west, lon), max(east, lon)
        south, north = min(south, lat), max(north, lat)
        found = True
    return (west, south, east, north) if found else None


def lon_deg_for_km(km, lat):
    return km / (KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(lat)))


def lon_km_for_deg(deg, lat):
    return deg * KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(lat))


def valid_area(area):
    b = area["candidateBbox"]
    radius_km = area["radiusM"] / 1000.0
    lat_ref = max(abs(b["south"]), abs(b["north"]))
    dlat = radius_km / KM_PER_DEG_LAT
    dlon = lon_deg_for_km(radius_km, lat_ref)
    result = {
        "west": round(b["west"] + dlon, 6),
        "south": round(b["south"] + dlat, 6),
        "east": round(b["east"] - dlon, 6),
        "north": round(b["north"] - dlat, 6),
    }
    if not (result["west"] < result["east"] and result["south"] < result["north"]):
        raise SystemExit("candidateBbox is too small for radiusM")
    return result


def write_summary(lines):
    output = "\n".join(lines) + "\n"
    print(output)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(output)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_bbox_arg(args):
    b = load_area(args.area)["candidateBbox"]
    print(f'{b["west"]},{b["south"]},{b["east"]},{b["north"]}')


def cmd_ward_filter(args):
    area = load_area(args.area)
    names = area["wards"]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("r/name=" + ",".join(names) + "\n")


def cmd_ward_check(args):
    area = load_area(args.area)
    candidate = area["candidateBbox"]
    usable = valid_area(area)
    wards = area["wards"]
    preferred = str(area.get("wardPreferredAdminLevel", ""))
    candidates = {name: [] for name in wards}

    for feature in iter_geojsonseq(args.geojsonseq):
        props = feature.get("properties") or {}
        if props.get("@type") != "relation":
            continue
        name = props.get("name")
        if name not in candidates or props.get("boundary") != "administrative":
            continue
        bbox = geometry_bbox(feature.get("geometry"))
        if bbox:
            candidates[name].append({
                "relationId": str(props.get("@id")),
                "adminLevel": str(props.get("admin_level", "")),
                "bbox": bbox,
            })

    results = []
    errors = []
    for name in wards:
        found = candidates[name]
        if len(found) > 1:
            preferred_matches = [c for c in found if c["adminLevel"] == preferred]
            if len(preferred_matches) == 1:
                found = preferred_matches
        if len(found) != 1:
            errors.append(f"{name}: boundary relation candidates = {len(found)}")
            results.append({"name": name, "found": False})
            continue

        item = found[0]
        west, south, east, north = item["bbox"]
        inside = (
            west >= usable["west"] and south >= usable["south"]
            and east <= usable["east"] and north <= usable["north"]
        )
        if not inside:
            errors.append(f"{name}: boundary is outside the valid area")
        results.append({
            "name": name,
            "found": True,
            "relationId": item["relationId"],
            "adminLevel": item["adminLevel"],
            "bbox": {
                "west": round(west, 6), "south": round(south, 6),
                "east": round(east, 6), "north": round(north, 6),
            },
            "insideValidArea": inside,
        })

    boxes = [r["bbox"] for r in results if r.get("found")]
    union = None
    margins = None
    if boxes:
        union = {
            "west": min(b["west"] for b in boxes),
            "south": min(b["south"] for b in boxes),
            "east": max(b["east"] for b in boxes),
            "north": max(b["north"] for b in boxes),
        }
        lat_ref = max(abs(candidate["south"]), abs(candidate["north"]))
        radius_km = area["radiusM"] / 1000.0
        raw = {
            "west": lon_km_for_deg(union["west"] - candidate["west"], lat_ref),
            "east": lon_km_for_deg(candidate["east"] - union["east"], lat_ref),
            "south": (union["south"] - candidate["south"]) * KM_PER_DEG_LAT,
            "north": (candidate["north"] - union["north"]) * KM_PER_DEG_LAT,
        }
        margins = {
            key: {"marginKm": round(value, 3), "afterRadiusKm": round(value - radius_km, 3)}
            for key, value in raw.items()
        }

    passed = not errors and len(boxes) == len(wards)
    output = {
        "passed": passed,
        "candidateBbox": candidate,
        "validArea": usable,
        "radiusM": area["radiusM"],
        "wardUnionBbox": union,
        "marginsKm": margins,
        "wards": results,
        "errors": errors,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    lines = [
        "## Ward boundary check",
        "",
        f"- Result: **{'PASS' if passed else 'FAIL'}**",
        f"- Candidate bbox: {candidate}",
        f"- Valid area after {area['radiusM']}m inset: {usable}",
    ]
    for row in results:
        lines.append(f"- {row['name']}: {'OK' if row.get('insideValidArea') else 'NG'}")
    if errors:
        lines += ["", "### Errors"] + [f"- {error}" for error in errors]
    write_summary(lines)
    if not passed:
        sys.exit(1)


def cmd_build(args):
    area = load_area(args.area)
    candidate = area["candidateBbox"]
    checks = area["checks"]
    with open(args.ward_check, "r", encoding="utf-8") as f:
        ward = json.load(f)

    stats = {
        "features": 0, "skippedNoTypeId": 0, "skippedNoMatch": 0,
        "skippedNoGeometry": 0, "skippedOutsideBbox": 0, "duplicates": 0,
    }
    seen = set()
    elements = []

    for feature in iter_geojsonseq(args.geojsonseq):
        stats["features"] += 1
        props = feature.get("properties") or {}
        osm_type = props.get("@type")
        osm_id = props.get("@id")
        if osm_type not in TYPE_ORDER or osm_id is None:
            stats["skippedNoTypeId"] += 1
            continue
        tags = {k: v for k, v in props.items() if not k.startswith("@")}
        if classify(tags) is None:
            stats["skippedNoMatch"] += 1
            continue
        bbox = geometry_bbox(feature.get("geometry"))
        if bbox is None:
            stats["skippedNoGeometry"] += 1
            continue
        lon = (bbox[0] + bbox[2]) / 2.0
        lat = (bbox[1] + bbox[3]) / 2.0
        if not (
            candidate["west"] <= lon <= candidate["east"]
            and candidate["south"] <= lat <= candidate["north"]
        ):
            stats["skippedOutsideBbox"] += 1
            continue
        key = (osm_type, str(osm_id))
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        kept = {}
        for tag_key in KEEP_TAG_KEYS:
            value = tags.get(tag_key)
            if isinstance(value, str) and value:
                kept[tag_key] = value
        elements.append({
            "type": osm_type,
            "id": str(osm_id),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "tags": kept,
        })

    elements.sort(key=lambda el: (TYPE_ORDER[el["type"]], int(el["id"])))
    by_category = {category: 0 for category in CATEGORY_ORDER}
    by_type = {osm_type: 0 for osm_type in TYPE_ORDER}
    for element in elements:
        by_category[classify(element["tags"])] += 1
        by_type[element["type"]] += 1

    facilities = {
        "schemaVersion": SCHEMA_VERSION,
        "sourceTimestamp": args.source_timestamp or "",
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "licenseUrl": LICENSE_URL,
        "elements": elements,
    }
    data_bytes = json.dumps(
        facilities, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    os.makedirs(args.out_dir, exist_ok=True)
    data_path = os.path.join(args.out_dir, "facilities.json")
    with open(data_path, "wb") as f:
        f.write(data_bytes)

    errors = []
    if not ward.get("passed"):
        errors.append("wardCheck did not pass")
    with open(data_path, "rb") as f:
        disk_bytes = f.read()
    sha256 = hashlib.sha256(disk_bytes).hexdigest()

    try:
        reparsed = json.loads(disk_bytes.decode("utf-8"))
        if len(reparsed.get("elements", [])) != len(elements):
            errors.append("re-read element count mismatch")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"facilities.json cannot be re-read: {exc}")

    if len(elements) < checks["minTotal"]:
        errors.append(f"total {len(elements)} < minTotal {checks['minTotal']}")
    for category in CATEGORY_ORDER:
        if by_category[category] < checks["minPerCategory"]:
            errors.append(
                f"category {category}: {by_category[category]} < minPerCategory {checks['minPerCategory']}"
            )
    if len(disk_bytes) > checks["maxDataBytes"]:
        errors.append(
            f"facilities.json {len(disk_bytes)} bytes > maxDataBytes {checks['maxDataBytes']}"
        )

    server = os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "dryRun": args.dry_run == "true",
        "bboxStatus": area.get("bboxStatus", ""),
        "source": {
            "url": area["source"]["pbfUrl"],
            "md5": args.source_md5 or "",
            "timestamp": args.source_timestamp or "",
        },
        "bbox": candidate,
        "validArea": ward.get("validArea"),
        "radiusM": area["radiusM"],
        "tagConditions": [
            "shop=supermarket", "shop=convenience", "amenity=hospital",
            "amenity=pharmacy", "amenity=kindergarten|childcare",
            "amenity=school", "highway=bus_stop",
        ],
        "categoryOrder": CATEGORY_ORDER,
        "counts": {
            "total": len(elements), "byCategory": by_category, "byType": by_type,
        },
        "buildStats": stats,
        "wardCheck": {
            "passed": ward.get("passed"),
            "wardUnionBbox": ward.get("wardUnionBbox"),
            "marginsKm": ward.get("marginsKm"),
            "wards": ward.get("wards"),
        },
        "dataFile": "facilities.json",
        "dataBytes": len(disk_bytes),
        "sha256": sha256,
        "attribution": ATTRIBUTION,
        "copyrightUrl": COPYRIGHT_URL,
        "license": LICENSE,
        "licenseUrl": LICENSE_URL,
        "osmiumVersion": args.osmium_version or "",
        "runUrl": f"{server}/{repository}/actions/runs/{run_id}" if run_id else "",
        "validationErrors": errors,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    lines = [
        "## Facility build result",
        "",
        f"- Result: **{'PASS' if not errors else 'FAIL'}**",
        f"- dry_run: {args.dry_run}",
        f"- Total: {len(elements)}",
        f"- facilities.json: {len(disk_bytes):,} bytes",
        f"- SHA-256: \`{sha256}\`",
    ]
    for category in CATEGORY_ORDER:
        lines.append(f"- {category}: {by_category[category]}")
    if errors:
        lines += ["", "### Errors"] + [f"- {error}" for error in errors]
    write_summary(lines)
    if errors:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("bbox-arg")
    command.add_argument("--area", required=True)
    command.set_defaults(func=cmd_bbox_arg)

    command = sub.add_parser("ward-filter")
    command.add_argument("--area", required=True)
    command.add_argument("--out", required=True)
    command.set_defaults(func=cmd_ward_filter)

    command = sub.add_parser("ward-check")
    command.add_argument("--area", required=True)
    command.add_argument("--geojsonseq", required=True)
    command.add_argument("--out", required=True)
    command.set_defaults(func=cmd_ward_check)

    command = sub.add_parser("build")
    command.add_argument("--area", required=True)
    command.add_argument("--geojsonseq", required=True)
    command.add_argument("--ward-check", required=True)
    command.add_argument("--out-dir", required=True)
    command.add_argument("--source-timestamp", default="")
    command.add_argument("--source-md5", default="")
    command.add_argument("--osmium-version", default="")
    command.add_argument("--dry-run", choices=["true", "false"], required=True)
    command.set_defaults(func=cmd_build)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
