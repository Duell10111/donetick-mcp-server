"""Configuration management for Donetick MCP server."""

import logging
import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Configuration for Donetick MCP server.

    Values are read from the environment on construction. Validation is not
    performed automatically so that importing the package (e.g. in tests) does
    not require credentials; call ``validate()`` before starting the server.
    """

    def __init__(self):
        """Initialize configuration from environment variables."""
        self.donetick_base_url = os.getenv("DONETICK_BASE_URL")
        if self.donetick_base_url:
            self.donetick_base_url = self.donetick_base_url.rstrip("/")
        self.donetick_username = os.getenv("DONETICK_USERNAME")
        self.donetick_password = os.getenv("DONETICK_PASSWORD")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.rate_limit_per_second = float(os.getenv("RATE_LIMIT_PER_SECOND", "10.0"))
        self.rate_limit_burst = int(os.getenv("RATE_LIMIT_BURST", "10"))

        # Donetick supports long-lived API tokens ("secretkey" header), but not for
        # all endpoints (labels and users are JWT-only). Not supported here yet.
        self.donetick_api_token = os.getenv("DONETICK_API_TOKEN")

    def validate(self):
        """Validate that required configuration is present and secure.

        Raises:
            ValueError: If required settings are missing or insecure
        """
        errors = []

        # Check base URL
        if not self.donetick_base_url:
            errors.append(
                "DONETICK_BASE_URL environment variable is required. "
                "Please set it to your Donetick instance URL."
            )
        elif not self.donetick_base_url.startswith("https://"):
            # Enforce HTTPS for security
            errors.append(
                f"DONETICK_BASE_URL must use HTTPS for security. "
                f"Got: {self.donetick_base_url[:50]}"
            )

        if not self.donetick_username:
            errors.append(
                "DONETICK_USERNAME environment variable is required. "
                "Please set it to your Donetick account username."
            )

        if not self.donetick_password:
            errors.append(
                "DONETICK_PASSWORD environment variable is required. "
                "Please set it to your Donetick account password."
            )

        if self.donetick_api_token:
            logging.getLogger(__name__).warning(
                "DONETICK_API_TOKEN is set but API token authentication is not supported yet "
                "(Donetick does not accept API tokens for labels and user endpoints). "
                "The server authenticates with DONETICK_USERNAME and DONETICK_PASSWORD."
            )

        if errors:
            raise ValueError(
                "Configuration validation failed:\n" +
                "\n".join(f"  - {error}" for error in errors)
            )

    def configure_logging(self):
        """Configure logging based on log level."""
        log_level = getattr(logging, self.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


# Global configuration instance
config = Config()
