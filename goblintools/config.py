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

@dataclass
class GoblinConfig:
    """Main configuration class for GoblinTools"""
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    ocr: OCRConfig = None
    
    def __post_init__(self):
        if self.ocr is None:
            self.ocr = OCRConfig()
    
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
        
        return cls(ocr=ocr_config, **data)
    
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