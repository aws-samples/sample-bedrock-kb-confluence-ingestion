# Bug Report: Attachment Downloads Completely Broken on Confluence Cloud

**Date**: 2026-05-20  
**Severity**: High — ALL image/attachment processing is non-functional  
**Affected files**:
- `src/ckn_ingestion/confluence_extractor.py`
- `src/ckn_ingestion/image_processor.py`
- `src/ckn_ingestion/models.py`

---

## Summary

The ingestion pipeline cannot download ANY attachments from Confluence Cloud. The 22 `.drawio` warnings in the logs are the visible symptom, but the actual impact is much larger: **all PNG/JPG/GIF attachments are silently skipped** due to a separate bug that masks the download failure.

---

## Bug 1: Empty `mediaType` — All Image Attachments Silently Skipped

### Problem

The v1 REST API (`/wiki/rest/api/content/{pageId}/child/attachment`) does NOT return `mediaType` at the top level of attachment results. It's always an empty string.

### Evidence

```python
# v1 API response (what the code uses):
{"title": "image2023-5-31_9-49-20.png", "mediaType": "", ...}

# v1 API with expand=extensions:
{"title": "image2023-5-31_9-49-20.png", "mediaType": "", "extensions": {"mediaType": "application/octet-stream", "fileSize": 357884}, ...}

# v2 API response (returns mediaType correctly):
{"title": "image2023-5-31_9-49-20.png", "mediaType": "image/png", ...}
```

### Root Cause

In `confluence_extractor.py` line 154:
```python
media_type=result.get("mediaType", ""),  # Always returns ""
```

The `_list_attachments()` function does not pass `expand=extensions` in the API call params, so `mediaType` is never populated.

### Impact

In `image_processor.py`, the `process_page_images()` function checks:
```python
if media_type not in PROCESSABLE_MEDIA_TYPES and not is_drawio:
    continue  # Skips ALL attachments with empty mediaType
```

Since `""` is not in `PROCESSABLE_MEDIA_TYPES` (`{"image/png", "image/jpeg", "image/gif", "image/svg+xml"}`), every PNG/JPG/GIF attachment is silently skipped. Only `.drawio` files pass (matched by filename extension).

### Fix

**Option A** — Add `expand=extensions` to the v1 API call and read from extensions:
```python
# In _list_attachments():
params: dict[str, Any] = {"limit": _PAGE_LIMIT, "start": 0, "expand": "extensions"}

# When building Attachment:
media_type=result.get("extensions", {}).get("mediaType", ""),
```

**Option B** — Switch to the v2 API which returns `mediaType` at the top level:
```python
url = f"{base_url}/wiki/api/v2/pages/{page_id}/attachments"
# v2 returns mediaType directly in each result
```

**Option C** — Infer media type from filename extension as a fallback:
```python
import mimetypes
media_type = result.get("mediaType", "") or mimetypes.guess_type(filename)[0] or ""
```

---

## Bug 2: Wrong Download URL — All Attachment Downloads Return HTTP 401

### Problem

The code constructs download URLs using the `_links.download` path from the API response. This path (`/download/attachments/{pageId}/{filename}?...`) is a **legacy web UI endpoint** that does NOT accept API token authentication (Basic auth or Bearer token).

### Evidence

```python
# What the code constructs (confluence_extractor.py line 154):
download_url = f"{base_url}{download_path}"
# Result: https://your-confluence.atlassian.net/download/attachments/123456789/file.png?version=1&...
# Response: HTTP 401

# What works (confirmed via live testing):
# GET /wiki/rest/api/content/{pageId}/child/attachment/{attachmentId}/download
# Result: HTTP 302 → redirect to signed api.media.atlassian.com URL → HTTP 200 with binary content
```

### Root Cause

The `/download/attachments/` endpoint is a web UI path that requires browser session cookies. It does not accept:
- Basic auth (email + API token) → 401
- Bearer token (API token) → 200 but returns HTML login page
- OAuth2 Bearer token → 401 (confirmed Atlassian bug CONFCLOUD-75771)

### Atlassian Documentation

The correct endpoint for programmatic downloads is documented in the v1 REST API:

**Endpoint**: `GET /wiki/rest/api/content/{id}/child/attachment/{attachmentId}/download`  
**Docs**: https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content---attachments/#api-wiki-rest-api-content-id-child-attachment-attachmentid-download-get  
**Behaviour**: Returns HTTP 302 redirect to a signed media URL  
**Auth**: Basic auth with email + API token ✅  
**Scopes**: `readonly:content.attachment:confluence` (Classic) or `read:attachment:confluence` (Granular)

### Community Confirmation

- **Jira bug**: https://jira.atlassian.com/browse/CONFCLOUD-75771 — "Download attachments endpoint (/wiki/download/attachments/<id>) does not accept Bearer token authentication" — Closed as "Timed out" (won't fix)
- **Community solution**: https://community.atlassian.com/forums/Confluence-questions/Attachment-dowload-authentication/qaq-p/3142082 — User `blatzfab` confirmed the fix: "By using this endpoint: `<BASE_URL>/rest/api/content/<PAGE_ID>/child/attachment/<ATTACHMENT_ID>/download` it works now." (confirmed by `Dilip Yadav`, May 2026)
- **Developer community**: https://community.developer.atlassian.com/t/download-attachment-from-confluence-page/76017 — Atlassian staff (`ibuchanan`) confirmed: use the v1 API endpoint for downloads, v2 does not have an equivalent.

### Live Test Results

```
# BROKEN (current code approach):
GET https://your-confluence.atlassian.net/download/attachments/123456789/image.png?...
→ HTTP 401

# WORKING (correct REST API endpoint):
GET https://your-confluence.atlassian.net/wiki/rest/api/content/123456789/child/attachment/att987654321/download
→ HTTP 302 → https://api.media.atlassian.com/file/{uuid}/binary?token=...
→ HTTP 200, Content-Type: image/png, 89173 bytes ✅
```

### Fix

The `_download_attachment()` function in `image_processor.py` must use the REST API download endpoint instead of the `_links.download` URL.

This requires a **data model change**: the `Attachment` model needs to store `page_id` (or the full REST download URL must be pre-built in `confluence_extractor.py`).

**Option A** — Pre-build the correct URL in `confluence_extractor.py`:
```python
# In _list_attachments():
download_url = f"{base_url}/wiki/rest/api/content/{page_id}/child/attachment/{result['id']}/download"
```

**Option B** — Store page_id in the Attachment model and build the URL at download time:
```python
# In models.py, add page_id to Attachment
# In image_processor.py, construct:
url = f"{base_url}/wiki/rest/api/content/{attachment.page_id}/child/attachment/{attachment.id}/download"
```

**Important**: The REST API endpoint returns a **302 redirect**. The `requests.get()` call with `allow_redirects=True` (default) will follow it automatically, so no code change needed for redirect handling.

---

## Bug 3: Archived Attachments Return 404

### Problem

Some attachments have `status: archived` in Confluence. The REST API download endpoint returns 404 for these:

```json
{
  "statusCode": 404,
  "message": "No content found with id : ContentId{id=42764426} and status [current], there is a content object with status : archived"
}
```

### Evidence

Page `42764426` and all its attachments are archived. The v1 API still lists them (without filtering by status), but the download endpoint rejects them.

### Impact

5 of the 22 failing `.drawio` files are on archived pages. After fixing Bugs 1 and 2, these will still fail with 404 instead of 401.

### Fix

**Option A** — Filter out archived attachments during listing:
```python
# In _list_attachments(), skip archived:
if result.get("status") == "archived":
    continue
```

**Option B** — Handle 404 gracefully in the download function (already partially done via the generic exception handler, but should log a more specific message).

---

## Additional Context

### Why Only 22 Warnings Were Logged (Not Hundreds)

The interaction of Bug 1 and Bug 2 creates a masking effect:

1. Bug 1 causes all PNG/JPG/GIF attachments to be **silently skipped** (empty mediaType → not in PROCESSABLE_MEDIA_TYPES → `continue`)
2. Only `.drawio` files pass the filter (matched by filename extension, not mediaType)
3. Bug 2 then causes those `.drawio` downloads to fail with HTTPError (401)
4. Result: only 22 warnings for `.drawio` files, while hundreds of image attachments are silently ignored

### The `drawio` CLI Dependency

Even after fixing the download bugs, the `process_drawio_attachment()` function requires the `drawio` CLI tool to export `.drawio` → PNG. Verify this is installed in the Docker image:
```dockerfile
# Check if drawio/draw.io is available in the container
RUN which drawio || echo "drawio CLI not installed"
```

### Metadata Enrichment Side Effect

The `has_images` flag in metadata is set based on attachment presence:
```python
has_images = any(
    a.media_type in PROCESSABLE_MEDIA_TYPES or a.filename.lower().endswith(".drawio")
    for a in page.attachments
)
```

With Bug 1 (empty mediaType), `has_images` is only `True` for pages with `.drawio` attachments. After fixing Bug 1, many more pages will have `has_images=True` and will attempt image processing (increasing Bedrock Vision API calls and runtime).

---

## Recommended Fix Order

1. **Bug 2 first** (download URL) — This is the blocking issue. Without it, no downloads work.
2. **Bug 1 second** (mediaType) — After fixing downloads, this enables PNG/JPG/GIF processing.
3. **Bug 3 last** (archived) — Graceful handling of edge case.

## Files to Modify

| File | Changes |
|------|---------|
| `src/ckn_ingestion/models.py` | Add `page_id: str` field to `Attachment` dataclass (needed for REST API download URL) |
| `src/ckn_ingestion/confluence_extractor.py` | 1. Add `expand=extensions` to `_list_attachments()` params<br>2. Read `mediaType` from `extensions` dict<br>3. Build correct download URL using REST API path<br>4. Pass `page_id` to Attachment constructor<br>5. Filter archived attachments |
| `src/ckn_ingestion/image_processor.py` | 1. Update `_download_attachment()` to handle 302 redirects (already default in requests)<br>2. Remove the manual URL construction if using pre-built URLs<br>3. Add specific handling for 404 (archived content) |

---

## Test Verification

After fixes, verify with:
```bash
# From the ECS task or locally with credentials:
python3 -c "
from ckn_ingestion.confluence_extractor import get_confluence_token, extract_pages
from ckn_ingestion.config import load_config
# Test that attachments have correct mediaType and download_url
"
```

Or run the existing test suite which should be updated to cover:
- `test_list_attachments_returns_media_type` — verify mediaType is populated
- `test_download_url_uses_rest_api_endpoint` — verify URL format
- `test_archived_attachments_handled_gracefully` — verify no crash on 404
