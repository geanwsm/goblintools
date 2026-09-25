import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Union, Dict, Any

@dataclass
class OCRConfig:
    """Configuration for OCR processing"""
    use_aws: bool = False
    aws_access_key: Optional[str] = None
    aws_secret_key: Optional[str] = None
    aws_region: str = 'us-east-1'
    tesseract_lang: str = 'por'
    # Appended (not inserted) so positional OCRConfig(...) calls keep working.
    aws_session_token: Optional[str] = None
    # Opt-in: with use_aws=True and no explicit keys, let boto3 resolve credentials
    # itself (env, profile, ECS task role) instead of falling back to Tesseract.
    use_default_aws_credentials: bool = False
    # PDF OCR rasterizes one page at a time at this resolution (200 = pdf2image's
    # own default, so existing results are unchanged). max_ocr_pages=None OCRs
    # every page; set a cap to bound cost on huge scans (pages beyond it are skipped).
    pdf_ocr_dpi: int = 200
    max_ocr_pages: Optional[int] = None

@dataclass
class ArchiveLimits:
    """Caps applied while extracting archives (decompression-bomb guard).

    Defaults are deliberately high so existing callers keep their results and
    only abuse is stopped; consumers handling untrusted uploads should tighten
    them (e.g. 3 / 40 / 25 MB / 80 MB). ``max_depth`` counts archive levels
    opened, the top-level archive included.
    """
    max_depth: int = 3
    max_members: int = 500
    max_member_bytes: int = 200 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024


@dataclass
class GoblinConfig:
    """Main configuration class for GoblinTools"""
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    ocr: OCRConfig = None
    archive_limits: ArchiveLimits = None

    def __post_init__(self):
        if self.ocr is None:
            self.ocr = OCRConfig()
        if self.archive_limits is None:
            self.archive_limits = ArchiveLimits()
    
    @classmethod
    def from_file(cls, config_path: Union[str, Path]) -> 'GoblinConfig':
        """Load configuration from JSON file"""
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            data = json.load(f)
        
        # Handle nested OCR config
        ocr_data = data.pop('ocr', {})
        ocr_config = OCRConfig(**ocr_data)
        limits_data = data.pop('archive_limits', None) or {}

        return cls(ocr=ocr_config, archive_limits=ArchiveLimits(**limits_data), **data)
    
    def to_file(self, config_path: Union[str, Path]) -> None:
        """Save configuration to JSON file"""
        config_path = Path(config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = asdict(self)
        with open(config_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    @classmethod
    def default(cls) -> 'GoblinConfig':
        """Create default configuration"""
        return cls()