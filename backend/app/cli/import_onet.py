import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.taxonomy import (
    OccupationAlias,
    OccupationConcept,
    OccupationSkillLink,
    SkillAlias,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.services.skill_catalog import SKILL_DEFINITIONS
from app.services.taxonomy_normalization import normalize_search_query

logger = logging.getLogger("ai_hiring.cli.import_onet")

REQUIRED_FILES = {
    "occupation_data": "occupation_data.csv",
    "essential_skills": "essential_skills.csv",
    "transferable_skills": "transferable_skills.csv",
    "software_skills": "software_skills.csv",
}

OPTIONAL_FILES = {
    "job_titles": "job_titles.csv",
}

# Expected logical header columns (normalized to lowercase alphanumeric without punctuation)
EXPECTED_LOGICAL_HEADERS = {
    "occupation_data.csv": ["onetsoccode", "title", "description"],
    "essential_skills.csv": ["onetsoccode", "title", "elementid", "elementname", "scaleid", "datavalue"],
    "transferable_skills.csv": ["onetsoccode", "title", "elementid", "elementname", "scaleid", "datavalue"],
    "software_skills.csv": ["onetsoccode", "title", "workplaceexample", "elementid", "elementname"],
    "job_titles.csv": ["onetsoccode", "title", "jobtitle"],
}

BATCH_SIZE = 2000


class ImporterError(Exception):
    pass


def _normalize_header_name(header: str) -> str:
    """Normalizes header string to lowercase alphanumeric characters."""
    return "".join(ch.lower() for ch in header if ch.isalnum())


def _compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def _compute_combined_hash(hashes: Dict[str, str]) -> str:
    sorted_items = sorted(hashes.items(), key=lambda x: x[0])
    combined_str = ":".join(f"{k}={v}" for k, v in sorted_items)
    return hashlib.sha256(combined_str.encode("utf-8")).hexdigest().lower()


def _validate_headers(filepath: Path, required_logical: List[str]) -> Dict[str, int]:
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise ImporterError(f"File '{filepath.name}' is empty or missing headers.")

        norm_to_idx = {_normalize_header_name(h): idx for idx, h in enumerate(header)}
        missing = [req for req in required_logical if req not in norm_to_idx]
        if missing:
            raise ImporterError(
                f"Missing required logical column(s) {missing} in '{filepath.name}'. Found headers: {header}"
            )
        return norm_to_idx


def _parse_bool_flag(val: Optional[str]) -> bool:
    if not val:
        return False
    v = val.strip().upper()
    return v in ("Y", "YES", "TRUE", "1")


def import_onet_taxonomy(
    source_dir: Path,
    version: str = "31.0",
    session: Optional[Session] = None,
) -> int:
    """
    Imports O*NET 31.0 taxonomy CSV files offline, deterministically, and atomically into PostgreSQL.
    """
    close_session = False
    if session is None:
        session = SessionLocal()
        close_session = True

    try:
        # 1. Validate source directory exists
        if not source_dir.exists() or not source_dir.is_dir():
            logger.error("Source directory does not exist", extra={"source_dir": str(source_dir)})
            print(f"Error: Source directory '{source_dir}' does not exist.", file=sys.stderr)
            return 1

        file_paths: Dict[str, Path] = {}
        missing_files = []
        for key, fname in REQUIRED_FILES.items():
            fpath = (source_dir / fname).resolve()
            if not fpath.exists():
                missing_files.append(fname)
            else:
                file_paths[fname] = fpath

        if missing_files:
            logger.error("Missing required O*NET files", extra={"missing_files": missing_files})
            print(f"Error: Missing required O*NET CSV files in '{source_dir}': {missing_files}", file=sys.stderr)
            return 1

        # Check optional files (e.g. job_titles.csv)
        for key, fname in OPTIONAL_FILES.items():
            fpath = (source_dir / fname).resolve()
            if fpath.exists():
                file_paths[fname] = fpath

        # 2. Validate CSV headers with tolerant normalization
        header_maps: Dict[str, Dict[str, int]] = {}
        for fname, fpath in file_paths.items():
            logical_req = EXPECTED_LOGICAL_HEADERS.get(fname, [])
            try:
                header_maps[fname] = _validate_headers(fpath, logical_req)
            except ImporterError as e:
                logger.error(f"Header validation failed: {str(e)}")
                print(f"Error: {e}", file=sys.stderr)
                return 1

        # 3. Compute file and combined checksums
        file_hashes = {fname: _compute_sha256(fpath) for fname, fpath in file_paths.items()}
        combined_hash = _compute_combined_hash(file_hashes)
        now = datetime.now(timezone.utc)

        # 4. Check for existing snapshot with same version
        stmt = select(TaxonomySnapshot).where(
            TaxonomySnapshot.source == "ONET",
            TaxonomySnapshot.source_version == version,
        )
        existing_version_snapshots = session.execute(stmt).scalars().all()
        existing_identical_snapshot = next((s for s in existing_version_snapshots if s.combined_sha256_hash == combined_hash), None)

        if existing_version_snapshots and existing_identical_snapshot is None:
            logger.error(
                f"Snapshot for O*NET version {version} already exists with different content hash. Rejecting re-import.",
                extra={"version": version, "new_hash": combined_hash},
            )
            print(f"Error: Snapshot for O*NET {version} already exists with different content hash.")
            return 1

        if existing_identical_snapshot is not None:
            if existing_identical_snapshot.state == TaxonomySnapshotState.ACTIVE:
                logger.info(
                    "Identical active snapshot already exists. Skipping re-import.",
                    extra={"snapshot_id": str(existing_identical_snapshot.snapshot_id), "version": version},
                )
                print(f"Identical active O*NET {version} snapshot already exists ({existing_identical_snapshot.snapshot_id}).")
                return 0
            else:
                # Reactivate existing identical snapshot
                session.execute(
                    update(TaxonomySnapshot)
                    .where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
                    .values(state=TaxonomySnapshotState.SUPERSEDED, updated_at=now)
                )
                existing_identical_snapshot.state = TaxonomySnapshotState.ACTIVE
                existing_identical_snapshot.activated_at = now
                existing_identical_snapshot.updated_at = now
                session.commit()
                logger.info("Reactivated existing snapshot.", extra={"snapshot_id": str(existing_identical_snapshot.snapshot_id)})
                return 0

        # 5. Create new snapshot in IMPORTING state
        snapshot_id = uuid.uuid4()
        snapshot = TaxonomySnapshot(
            snapshot_id=snapshot_id,
            source="ONET",
            source_version=version,
            combined_sha256_hash=combined_hash,
            state=TaxonomySnapshotState.IMPORTING,
            source_filenames={k: str(p.name) for k, p in file_paths.items()},
            record_counts={},
            imported_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(snapshot)
        session.flush()

        # 6. Stream and import occupation_data.csv
        occupation_id_by_soc: Dict[str, uuid.UUID] = {}
        occupation_count = 0
        h_occ = header_maps["occupation_data.csv"]

        with open(file_paths["occupation_data.csv"], "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            batch = []
            for row in reader:
                if not row or len(row) <= max(h_occ.values()):
                    continue
                soc_code = row[h_occ["onetsoccode"]].strip()
                title = row[h_occ["title"]].strip()
                description = row[h_occ["description"]].strip() if "description" in h_occ and row[h_occ["description"]].strip() else None

                if not soc_code or not title:
                    continue

                norm_title = normalize_search_query(title)
                concept_id = uuid.uuid4()
                occupation_id_by_soc[soc_code] = concept_id

                occ = OccupationConcept(
                    concept_id=concept_id,
                    snapshot_id=snapshot_id,
                    onet_soc_code=soc_code,
                    canonical_title=title,
                    normalized_title=norm_title,
                    description=description,
                    source_provenance=f"ONET_{version}",
                    created_at=now,
                )
                batch.append(occ)
                occupation_count += 1
                if len(batch) >= BATCH_SIZE:
                    session.add_all(batch)
                    session.flush()
                    batch = []
            if batch:
                session.add_all(batch)
                session.flush()

        # 7. Stream and import job_titles.csv (Aliases) if present
        alias_count = 0
        seen_aliases: Set[Tuple[uuid.UUID, str]] = set()

        if "job_titles.csv" in file_paths:
            h_jt = header_maps["job_titles.csv"]
            with open(file_paths["job_titles.csv"], "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                batch = []
                for row in reader:
                    if not row or len(row) <= max(h_jt.values()):
                        continue
                    soc_code = row[h_jt["onetsoccode"]].strip()
                    alias_title = row[h_jt["jobtitle"]].strip()

                    if not soc_code or not alias_title:
                        continue

                    occ_id = occupation_id_by_soc.get(soc_code)
                    if not occ_id:
                        continue

                    norm_alias = normalize_search_query(alias_title)
                    if (occ_id, norm_alias) in seen_aliases:
                        continue
                    seen_aliases.add((occ_id, norm_alias))

                    alias = OccupationAlias(
                        alias_id=uuid.uuid4(),
                        occupation_concept_id=occ_id,
                        job_title_alias=alias_title,
                        normalized_alias=norm_alias,
                        source_provenance=f"ONET_{version}_JOB_TITLES",
                        created_at=now,
                    )
                    batch.append(alias)
                    alias_count += 1
                    if len(batch) >= BATCH_SIZE:
                        session.add_all(batch)
                        session.flush()
                        batch = []
                if batch:
                    session.add_all(batch)
                    session.flush()

        # 8. Seed Controlled Skill Concepts & Aliases from SkillCatalog
        skill_concept_by_norm_label: Dict[str, uuid.UUID] = {}
        skill_concept_by_element_id: Dict[str, uuid.UUID] = {}
        skill_concept_count = 0
        skill_alias_count = 0

        for skill_def in SKILL_DEFINITIONS:
            norm_label = normalize_search_query(skill_def.canonical_name)
            if norm_label in skill_concept_by_norm_label:
                continue

            concept_id = uuid.uuid4()
            skill_concept_by_norm_label[norm_label] = concept_id

            sk = SkillConcept(
                concept_id=concept_id,
                snapshot_id=snapshot_id,
                canonical_label=skill_def.canonical_name,
                normalized_label=norm_label,
                category=skill_def.category.value if hasattr(skill_def.category, "value") else str(skill_def.category),
                source="INTERNAL_CONTROLLED",
                source_family="software",
                created_at=now,
            )
            session.add(sk)
            skill_concept_count += 1

            # Seed aliases
            for al in skill_def.aliases:
                norm_al = normalize_search_query(al)
                if norm_al and norm_al != norm_label:
                    sk_alias = SkillAlias(
                        alias_id=uuid.uuid4(),
                        skill_concept_id=concept_id,
                        alias_text=al,
                        normalized_alias=norm_al,
                        alias_source="INTERNAL_CONTROLLED",
                        created_at=now,
                    )
                    session.add(sk_alias)
                    skill_alias_count += 1

        session.flush()

        # 9. Stream and import Content Model skills (Essential and Transferable)
        # Scale IDs in O*NET Content Model:
        # IM = Importance (0-100 or 1-5 scale)
        # LV = Level (0-7 scale)
        link_count = 0
        skipped_rows = 0
        # Cache for multi-scale links: (occ_id, skill_id) -> link dict
        content_links_map: Dict[Tuple[uuid.UUID, uuid.UUID], Dict[str, Any]] = {}

        def process_content_skill_file(filename: str, source_fam: str):
            nonlocal skill_concept_count, skipped_rows
            h_map = header_maps[filename]
            idx_soc = h_map["onetsoccode"]
            idx_elem_id = h_map["elementid"]
            idx_elem_name = h_map["elementname"]
            idx_scale_id = h_map["scaleid"]
            idx_data_val = h_map["datavalue"]
            idx_suppress = h_map.get("recommendsuppress")
            idx_not_rel = h_map.get("notrelevant")

            with open(file_paths[filename], "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                for row in reader:
                    if not row or len(row) <= max(idx_soc, idx_elem_id, idx_elem_name, idx_scale_id, idx_data_val):
                        skipped_rows += 1
                        continue

                    soc_code = row[idx_soc].strip()
                    element_id = row[idx_elem_id].strip()
                    element_name = row[idx_elem_name].strip()
                    scale_id = row[idx_scale_id].strip().upper()
                    data_val_str = row[idx_data_val].strip()

                    recommend_suppress = False
                    if idx_suppress is not None and len(row) > idx_suppress:
                        recommend_suppress = _parse_bool_flag(row[idx_suppress])

                    not_relevant = False
                    if idx_not_rel is not None and len(row) > idx_not_rel:
                        not_relevant = _parse_bool_flag(row[idx_not_rel])

                    if not soc_code or not element_id or not element_name:
                        skipped_rows += 1
                        continue

                    try:
                        data_val = float(data_val_str) if data_val_str else None
                    except ValueError:
                        data_val = None

                    occ_id = occupation_id_by_soc.get(soc_code)
                    if not occ_id:
                        skipped_rows += 1
                        continue

                    # Canonical skill by element_id or normalized label
                    norm_label = normalize_search_query(element_name)
                    skill_id = skill_concept_by_element_id.get(element_id) or skill_concept_by_norm_label.get(norm_label)

                    if not skill_id:
                        skill_id = uuid.uuid4()
                        skill_concept_by_element_id[element_id] = skill_id
                        skill_concept_by_norm_label[norm_label] = skill_id

                        sk = SkillConcept(
                            concept_id=skill_id,
                            snapshot_id=snapshot_id,
                            canonical_label=element_name,
                            normalized_label=norm_label,
                            category=source_fam.capitalize(),
                            source=f"ONET_{version}",
                            source_family=source_fam,
                            external_id=element_id,
                            created_at=now,
                        )
                        session.add(sk)
                        session.flush()
                        skill_concept_count += 1

                    link_key = (occ_id, skill_id)
                    if link_key not in content_links_map:
                        content_links_map[link_key] = {
                            "link_id": uuid.uuid4(),
                            "occupation_concept_id": occ_id,
                            "skill_concept_id": skill_id,
                            "source_family": source_fam,
                            "hot_technology": False,
                            "in_demand": False,
                            "importance_value": None,
                            "level_value": None,
                            "recommend_suppress": recommend_suppress,
                            "not_relevant": not_relevant,
                            "element_id": element_id,
                            "element_name": element_name,
                            "created_at": now,
                        }

                    link_entry = content_links_map[link_key]
                    if scale_id == "IM":
                        link_entry["importance_value"] = data_val
                    elif scale_id == "LV":
                        link_entry["level_value"] = data_val

                    if recommend_suppress:
                        link_entry["recommend_suppress"] = True
                    if not_relevant:
                        link_entry["not_relevant"] = True

        process_content_skill_file("essential_skills.csv", "essential")
        process_content_skill_file("transferable_skills.csv", "transferable")

        # Flush content model links in batches
        content_links_batch = []
        for l_dict in content_links_map.values():
            link = OccupationSkillLink(**l_dict)
            content_links_batch.append(link)
            link_count += 1
            if len(content_links_batch) >= BATCH_SIZE:
                session.add_all(content_links_batch)
                session.flush()
                content_links_batch = []
        if content_links_batch:
            session.add_all(content_links_batch)
            session.flush()

        # 10. Stream software_skills.csv (Software Skills & Links)
        seen_links: Set[Tuple[uuid.UUID, uuid.UUID]] = set(content_links_map.keys())
        h_sw = header_maps["software_skills.csv"]
        idx_sw_soc = h_sw["onetsoccode"]
        idx_sw_workplace = h_sw["workplaceexample"]
        idx_sw_elem_id = h_sw.get("elementid")
        idx_sw_elem_name = h_sw.get("elementname")
        idx_sw_hot = h_sw.get("hottechnology")
        idx_sw_indemand = h_sw.get("indemand")

        with open(file_paths["software_skills.csv"], "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            batch_links = []
            for row in reader:
                if not row or len(row) <= max(idx_sw_soc, idx_sw_workplace):
                    skipped_rows += 1
                    continue
                soc_code = row[idx_sw_soc].strip()
                skill_name = row[idx_sw_workplace].strip()
                element_id = row[idx_sw_elem_id].strip() if idx_sw_elem_id is not None and len(row) > idx_sw_elem_id and row[idx_sw_elem_id].strip() else None
                element_name = row[idx_sw_elem_name].strip() if idx_sw_elem_name is not None and len(row) > idx_sw_elem_name and row[idx_sw_elem_name].strip() else None
                hot_tech = _parse_bool_flag(row[idx_sw_hot]) if idx_sw_hot is not None and len(row) > idx_sw_hot else False
                in_demand = _parse_bool_flag(row[idx_sw_indemand]) if idx_sw_indemand is not None and len(row) > idx_sw_indemand else False

                if not soc_code or not skill_name:
                    skipped_rows += 1
                    continue

                occ_id = occupation_id_by_soc.get(soc_code)
                if not occ_id:
                    skipped_rows += 1
                    continue

                norm_skill = normalize_search_query(skill_name)
                skill_id = skill_concept_by_norm_label.get(norm_skill)

                if not skill_id:
                    skill_id = uuid.uuid4()
                    skill_concept_by_norm_label[norm_skill] = skill_id

                    sk = SkillConcept(
                        concept_id=skill_id,
                        snapshot_id=snapshot_id,
                        canonical_label=skill_name,
                        normalized_label=norm_skill,
                        category=element_name,
                        source=f"ONET_{version}",
                        source_family="software",
                        external_id=element_id,
                        created_at=now,
                    )
                    session.add(sk)
                    session.flush()
                    skill_concept_count += 1

                if (occ_id, skill_id) in seen_links:
                    continue
                seen_links.add((occ_id, skill_id))

                link = OccupationSkillLink(
                    link_id=uuid.uuid4(),
                    occupation_concept_id=occ_id,
                    skill_concept_id=skill_id,
                    source_family="software",
                    hot_technology=hot_tech,
                    in_demand=in_demand,
                    element_id=element_id,
                    element_name=element_name,
                    created_at=now,
                )
                batch_links.append(link)
                link_count += 1
                if len(batch_links) >= BATCH_SIZE:
                    session.add_all(batch_links)
                    session.flush()
                    batch_links = []

            if batch_links:
                session.add_all(batch_links)
                session.flush()

        # 11. Record counts, checksums, and metadata
        counts = {
            "occupations": occupation_count,
            "occupation_aliases": alias_count,
            "skills": skill_concept_count,
            "skill_aliases": skill_alias_count,
            "occupation_skill_links": link_count,
            "skipped_rows": skipped_rows,
        }
        snapshot.record_counts = counts

        # Supersede any previous ACTIVE snapshots
        session.execute(
            update(TaxonomySnapshot)
            .where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
            .values(state=TaxonomySnapshotState.SUPERSEDED, updated_at=now)
        )

        snapshot.state = TaxonomySnapshotState.ACTIVE
        snapshot.activated_at = now
        snapshot.updated_at = now

        session.commit()
        logger.info(
            f"Successfully imported O*NET {version} snapshot.",
            extra={"snapshot_id": str(snapshot_id), "record_counts": counts},
        )
        print(f"Successfully imported O*NET {version} snapshot {snapshot_id}: {counts}")
        return 0

    except Exception as e:
        session.rollback()
        logger.error(
            "Failed O*NET import transaction. Rolled back.",
            extra={"error_type": type(e).__name__},
        )
        print(f"Error during import: {e}", file=sys.stderr)
        return 1
    finally:
        if close_session:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Offline O*NET 31.0 Taxonomy Importer")
    parser.add_argument("--source-dir", required=True, type=str, help="Directory containing O*NET CSV files")
    parser.add_argument("--version", default="31.0", type=str, help="O*NET version (default: 31.0)")
    args = parser.parse_args()

    source_path = Path(args.source_dir).resolve()
    exit_code = import_onet_taxonomy(source_dir=source_path, version=args.version)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
