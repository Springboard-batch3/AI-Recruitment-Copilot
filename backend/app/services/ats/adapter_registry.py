from typing import Dict

from app.models.ats import ATSAdapterType
from app.services.ats.adapter_interface import ATSAdapter
from app.services.ats.csv_json_adapter import CSVJSONAdapter
from app.services.ats.local_folder_adapter import LocalFolderATSAdapter
from app.services.ats.mock_adapter import MockATSAdapter


_MOCK_ADAPTER = MockATSAdapter()
_CSV_JSON_ADAPTER = CSVJSONAdapter()
_LOCAL_FOLDER_ADAPTER = LocalFolderATSAdapter()

_STATIC_REGISTRY: Dict[ATSAdapterType, ATSAdapter] = {
    ATSAdapterType.MOCK: _MOCK_ADAPTER,
    ATSAdapterType.CSV: _CSV_JSON_ADAPTER,
    ATSAdapterType.JSON: _CSV_JSON_ADAPTER,
    ATSAdapterType.LOCAL_FOLDER_DEMO: _LOCAL_FOLDER_ADAPTER,
}


def get_ats_adapter(adapter_type: ATSAdapterType) -> ATSAdapter:
    """Return static reviewed adapter instance or raise ValueError.
    
    Prevents arbitrary dynamic loading, class paths, or unvetted adapter types.
    """
    if adapter_type not in _STATIC_REGISTRY:
        raise ValueError(f"Unknown or unvetted ATS adapter type: '{adapter_type}'.")
    return _STATIC_REGISTRY[adapter_type]
