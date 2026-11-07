"""Constants for the NESO Demand Flexibility Service integration."""

from zoneinfo import ZoneInfo

DOMAIN = "neso_dfs"
NAME = "NESO Demand Flexibility"
CSV_URL = (
    "https://api.neso.energy/dataset/829e325f-8926-40ab-9dd6-8b7aea6cb244/"
    "resource/3ebf77d7-05df-466e-a023-dc45a90efeea/download/"
    "dfs-utilisation-report.csv"
)
DATA_URL = (
    "https://www.neso.energy/data-portal/demand-flexibility/dfs_utilisation_report"
)
UK_TIMEZONE = ZoneInfo("Europe/London")
CONF_PARTICIPANT = "participant"
CONF_ZONE = "zone"
CONF_POLL_INTERVAL = "poll_interval"
CONF_RETENTION_DAYS = "retention_days"
DEFAULT_POLL_INTERVAL = 15
DEFAULT_RETENTION_DAYS = 30
REQUEST_TIMEOUT = 30
STORAGE_VERSION = 1
DOWNWARDS = "Downwards"
UPWARDS = "Upwards"
DIRECTIONS = (DOWNWARDS, UPWARDS)
