# Zenodo deposit workflow (maintainer reference)

Operational guide for cutting and publishing the Zenodo deposit that
accompanies this thesis. Reviewers do not need this document — they only
need [`docs/reproducibility.md`](reproducibility.md) §2 + 3. This file is for
the maintainer (Sevda) at each release point.

The deposit is the canonical archival home of the simulator + analysis artifacts
(~1.19 GB unpacked, ~0.9 GB compressed). See
[`docs/zenodo_metadata.json`](zenodo_metadata.json) for the metadata that
gets uploaded to the Zenodo form.

---

## 0. One-time setup

1. **Create a Zenodo account**: <https://zenodo.org/signup/>. Sign in with
   GitHub or ORCID to make later authoring easier.
2. **Generate a personal access token** (only needed if you want to script
   the upload via the REST API, which we do not for v1):
   <https://zenodo.org/account/settings/applications/> → New token,
   scope: `deposit:write deposit:actions`.
3. **Reserve a DOI in advance** (so it can be cited in the thesis manuscript
   *before* the deposit is published). On the Zenodo "New Upload" page:
   - Click **Reserve DOI** in the basic-information panel.
   - Note the reserved DOI (e.g. `10.5281/zenodo.12345678`).
   - Save the draft (do not publish yet).
   - Drop the DOI into the placeholders in:
     - `README.md` (`https://doi.org/10.5281/zenodo.XXXXXXX`)
     - `docs/reproducibility.md` (`## 3. Zenodo deposit ...`)
     - `scripts/fetch_zenodo_bundle.py` (`DEFAULT_DOI` constant).
   - Commit + push.

A reserved DOI does **not** activate until the deposit is published. It is
safe to print in the thesis PDF that goes out for examination — citing
authorities accept "DOI reserved at Zenodo, to be published on submission".

---

## 1. Per-release checklist (run from a clean repo)

Before every cut:

```bash
# 1. Ensure a clean tree (no uncommitted code that could affect outputs).
git status --porcelain    # must be empty

# 2. Full from-scratch regeneration of every artefact (MATLAB required).
make all-full             # ~3.5-4 h end-to-end (5-seed replication dominates)

# 3. Manifest refresh + commit.
make manifest             # data/manifest.sha256 now reflects fresh outputs
git add data/manifest.sha256
git commit -m "build(repro): regen manifest for vX.Y.Z deposit"

# 4. Tag the release commit so the bundle header echoes it.
git tag -a vX.Y.Z -m "Zenodo deposit cut for thesis defense"
git push origin main vX.Y.Z

# 5. Build the bundle tarball.
make zenodo-bundle        # writes dist/thesis_artifacts_vX.Y.Z.tar.gz

# 6. Verify the bundle round-trips: unpack into a scratch dir and
#    run verify-cache against the embedded manifest. Run the verifier
#    from the repo root so `tools.manifest` resolves; point it at the
#    scratch dir with --repo-root.
mkdir -p /tmp/zenodo-roundtrip
tar xzf dist/thesis_artifacts_vX.Y.Z.tar.gz -C /tmp/zenodo-roundtrip
python -m tools.manifest --verify \
    --manifest /tmp/zenodo-roundtrip/data/manifest.sha256 \
    --repo-root /tmp/zenodo-roundtrip
# expected tail: "manifest verify: 308/308 OK, 0 missing, 0 mismatch"
```

If step 6 fails, do not upload — fix the manifest drift first.

---

## 2. Uploading to Zenodo

### 2.1 Update the metadata file (if version changed)

Open [`docs/zenodo_metadata.json`](zenodo_metadata.json) and bump:

- `version`: e.g. `v1.0.0-rc1` -> `v1.0.0`,
- `description`: refresh the "file count" / "size" wording if the bundle
  scope changed (e.g. dropped or added a seeded variant).

Commit the bump.

### 2.2 Upload via the web UI (recommended for v1)

1. Go to your reserved draft at <https://zenodo.org/uploads> → the draft
   you reserved in step 0.3.
2. **Files**: drag in `dist/thesis_artifacts_vX.Y.Z.tar.gz`.
3. **Title, Authors, Description, Keywords, License, Related identifiers,
   Communities, Notes**: copy field-by-field from
   `docs/zenodo_metadata.json`. The fields map 1:1 to Zenodo form labels
   except:
   - JSON `upload_type: dataset` -> form **Resource type** = "Dataset".
   - JSON `access_right: open` -> form **Access** = "Open access".
   - JSON `related_identifiers[*].resource_type: software` -> form
     "Software" in the related-identifiers dropdown.
4. **Notes**: paste the bundle SHA256 printed by `make zenodo-bundle` so
   forensic verification is possible without the manifest.
5. **Save**, then **Publish** when ready. The DOI activates immediately on
   publish.

### 2.3 (Optional) Upload via REST API

For automation, see <https://developers.zenodo.org/#deposit-create> and
the inline comments in `scripts/build_zenodo_bundle.py`. Not implemented
in v1 because per-release human review of the deposit description is
desirable.

---

## 3. Post-upload tasks

```bash
# Replace 10.5281/zenodo.XXXXXXX with the published DOI everywhere.
grep -rn "10.5281/zenodo.XXXXXXX" .

# Update scripts/fetch_zenodo_bundle.py DEFAULT_DOI -> the real DOI.
# Commit + push.

# (Optional) Sanity-check the published deposit from a fresh checkout.
git clone https://github.com/<owner>/thesis.git /tmp/repro && cd /tmp/repro
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt
python -m scripts.fetch_zenodo_bundle --doi 10.5281/zenodo.<REAL>
make verify-cache         # 308/308 OK
make pytest               # 396 passed
```

If `make verify-cache` fails on a freshly-fetched deposit, the upstream
deposit is corrupted — re-cut from step 1.

---

## 4. Subsequent revisions

Zenodo's **New version** workflow (right sidebar on the published deposit
page) creates a sibling deposit with its own DOI and a shared "concept DOI"
that always points at the latest version.

When publishing a revised bundle:

1. Bump `docs/zenodo_metadata.json` `version` (e.g. `v1.0.0` -> `v1.0.1`)
   and refresh the description if file counts changed.
2. Repeat the §1 per-release checklist with the new tag.
3. On Zenodo, click **New version** on the published v1.0.0 record, attach
   the new tarball, publish.
4. Update `scripts/fetch_zenodo_bundle.py` `DEFAULT_DOI` to the concept DOI
   so `--doi` always resolves to the latest version.

The concept DOI is preferred in thesis citations because it remains valid
across all revisions.

---

## 5. Defense archival checklist

At the thesis defense tag (`v1.0.0` final):

- [ ] Reserved DOI in metadata + scripts (cross-checked via `grep XXXXXXX`).
- [ ] `make all-full` completed without manual intervention on a clean clone.
- [ ] `make manifest` committed; `git tag v1.0.0` pushed.
- [ ] `make zenodo-bundle` succeeded; bundle SHA256 recorded.
- [ ] Zenodo deposit published; DOI activated.
- [ ] DOI baked back into README + reproducibility doc + fetch script.
- [ ] `make verify-cache` on a fresh `python -m scripts.fetch_zenodo_bundle --doi <REAL>` succeeds.
- [ ] `docker build` + in-container `make pytest` green against the new lockfile.
- [ ] (Optional) Tarball mirrored to a second archive (the institutional
      repository, a personal cloud bucket, etc.) in case Zenodo has an outage
      during the defense.

The defense plate in `data/processed/end_to_end_demo_timeline_medium/end_to_end_moneyshot.png`
is the canonical Chapter 9 figure; verify it survives the round-trip by
opening it in an image viewer after the freshly-fetched deposit unpacks.
