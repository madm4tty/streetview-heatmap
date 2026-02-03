"""Configuration management with environment variable substitution.

Loads configuration from a YAML file and substitutes environment variables
using ${VAR} or ${VAR:-default} syntax.
"""

import os
import re
import yaml
from typing import Any, Dict, Optional
from pathlib import Path


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


class Config:
    """Configuration management with environment variable substitution."""

    # Pattern to match ${VAR} or ${VAR:-default}
    ENV_VAR_PATTERN = re.compile(r'\$\{([^}:]+)(?::-([^}]*))?\}')

    def __init__(self, config_path: str = "config.yaml"):
        """Load configuration from YAML file.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = Path(config_path)
        self.data: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load and parse configuration file with env var substitution."""
        if not self.config_path.exists():
            raise ConfigError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, 'r') as f:
            raw_content = f.read()

        # Substitute environment variables in the raw YAML content
        substituted = self._substitute_env_vars(raw_content)

        try:
            self.data = yaml.safe_load(substituted) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in {self.config_path}: {e}")

    def _substitute_env_vars(self, content: str) -> str:
        """Replace ${VAR} and ${VAR:-default} patterns with env values.

        Args:
            content: Raw YAML content with env var placeholders

        Returns:
            Content with environment variables substituted
        """
        def replace_match(match: re.Match) -> str:
            var_name = match.group(1)
            default_value = match.group(2) if match.group(2) is not None else ''

            env_value = os.environ.get(var_name)
            if env_value is not None:
                return env_value
            elif default_value:
                return default_value
            else:
                # Return empty string for unset vars without default
                return ''

        return self.ENV_VAR_PATTERN.sub(replace_match, content)

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation.

        Args:
            key: Dot-separated path (e.g., 'app.port', 'scheduler.enabled')
            default: Default value if key not found

        Returns:
            Configuration value or default

        Examples:
            config.get('app.port')  # Returns 5000
            config.get('app.missing', 'fallback')  # Returns 'fallback'
        """
        parts = key.split('.')
        value = self.data

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default

        return value

    def set(self, key: str, value: Any) -> None:
        """Set configuration value using dot notation.

        Args:
            key: Dot-separated path (e.g., 'app.port')
            value: Value to set
        """
        parts = key.split('.')
        current = self.data

        # Navigate to parent dict, creating intermediate dicts as needed
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]

        # Set the final value
        current[parts[-1]] = value

    def update(self, updates: Dict[str, Any]) -> None:
        """Update configuration values and optionally save to file.

        Args:
            updates: Dictionary of dot-notation keys to values
        """
        for key, value in updates.items():
            self.set(key, value)

    def save(self) -> None:
        """Save current configuration to file.

        Note: This will lose environment variable placeholders.
        Consider using for runtime updates only.
        """
        with open(self.config_path, 'w') as f:
            yaml.dump(self.data, f, default_flow_style=False, sort_keys=False)

    def validate(self) -> bool:
        """Validate that required configuration values exist.

        Returns:
            True if all required values are present

        Raises:
            ConfigError: If required values are missing
        """
        required = [
            ('database.url', 'DATABASE_URL environment variable'),
            ('google.api_key', 'GOOGLE_MAPS_API_KEY environment variable'),
        ]

        missing = []
        for key, description in required:
            value = self.get(key)
            if not value:
                missing.append(f"  - {key}: Set {description}")

        if missing:
            raise ConfigError(
                "Missing required configuration:\n" + "\n".join(missing)
            )

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Return configuration as a dictionary.

        Sensitive values (api keys, passwords) are masked.
        """
        def mask_sensitive(d: Dict, parent_key: str = '') -> Dict:
            result = {}
            sensitive_patterns = ['key', 'password', 'secret', 'token', 'url']

            for k, v in d.items():
                full_key = f"{parent_key}.{k}" if parent_key else k
                if isinstance(v, dict):
                    result[k] = mask_sensitive(v, full_key)
                elif isinstance(v, str) and any(p in k.lower() for p in sensitive_patterns):
                    # Mask sensitive string values
                    if v:
                        result[k] = v[:4] + '****' if len(v) > 4 else '****'
                    else:
                        result[k] = None
                else:
                    result[k] = v
            return result

        return mask_sensitive(self.data)

    def __repr__(self) -> str:
        return f"Config({self.config_path})"


def load_config(config_path: str = "config.yaml") -> Config:
    """Load configuration from file.

    Args:
        config_path: Path to configuration file

    Returns:
        Config instance
    """
    return Config(config_path)
