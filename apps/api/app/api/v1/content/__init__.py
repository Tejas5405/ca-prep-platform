"""Study content: the read library, uploads, document admin, and access rules.

Split from the former single-file app/api/v1/content.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order.

NOTE FOR TESTS. `storage_from_settings` is imported into each sub-module that uses
it, so patching `app.api.v1.content` no longer intercepts it. The content library
integration tests now patch each such sub-module; the stand-ins are unchanged."""

from __future__ import annotations

from fastapi import APIRouter

from . import access as _access
from . import documents as _documents
from . import library as _library
from . import uploads as _uploads
from ._shared import MAX_BATCH_FILES, MAX_BULK_SELECTION, DocumentKindRef, logger
from .access import (
    AccessRuleIn,
    add_access_rule,
    delete_access_rule,
    list_access_rules,
)
from .documents import (
    BulkMetadataIn,
    UpdateDocumentIn,
    admin_download,
    admin_list_documents,
    admin_view_text,
    archive_document,
    batch_progress,
    bulk_metadata,
    reprocess_document,
    restore_document,
    update_document,
)
from .library import (
    get_library_document,
    get_library_file,
    get_library_pages,
    list_library,
    search_library,
)
from .uploads import CreateUploadsIn, UploadFileIn, create_uploads, start_processing

router = APIRouter()
router.routes.extend(_library.router.routes)
router.routes.extend(_uploads.router.routes)
router.routes.extend(_documents.router.routes)
router.routes.extend(_access.router.routes)

__all__ = [
    "MAX_BATCH_FILES",
    "MAX_BULK_SELECTION",
    "AccessRuleIn",
    "BulkMetadataIn",
    "CreateUploadsIn",
    "DocumentKindRef",
    "UpdateDocumentIn",
    "UploadFileIn",
    "add_access_rule",
    "admin_download",
    "admin_list_documents",
    "admin_view_text",
    "archive_document",
    "batch_progress",
    "bulk_metadata",
    "create_uploads",
    "delete_access_rule",
    "get_library_document",
    "get_library_file",
    "get_library_pages",
    "list_access_rules",
    "list_library",
    "logger",
    "reprocess_document",
    "restore_document",
    "router",
    "search_library",
    "start_processing",
    "update_document",
]
