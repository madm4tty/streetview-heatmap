"""Pydantic models for request/response validation.

These models define the data structures for API requests and responses,
providing automatic validation and serialization.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator


class Priority(str, Enum):
    """Priority levels for tiles and processing."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JobStatus(str, Enum):
    """Status of a background job."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# Request Models
# ============================================================================

class TriggerUpdateRequest(BaseModel):
    """Request body for triggering an update job."""
    priority: Optional[Priority] = Field(
        None,
        description="Filter tiles by priority level"
    )
    tile_limit: Optional[int] = Field(
        None,
        ge=1,
        le=1000,
        description="Maximum tiles to process"
    )


class ConfigUpdateRequest(BaseModel):
    """Request body for updating configuration."""
    scheduler: Optional[Dict[str, Any]] = Field(
        None,
        description="Scheduler configuration updates"
    )
    update: Optional[Dict[str, Any]] = Field(
        None,
        description="Update job configuration"
    )

    @field_validator('update')
    @classmethod
    def validate_update_config(cls, v):
        if v is not None:
            allowed_keys = {
                'batch_size', 'concurrency', 'min_age_for_recheck_days',
                'overpass_delay_seconds', 'samples_per_road', 'adaptive_sampling'
            }
            invalid_keys = set(v.keys()) - allowed_keys
            if invalid_keys:
                raise ValueError(f"Invalid update config keys: {invalid_keys}")
        return v


class TilesQueryParams(BaseModel):
    """Query parameters for listing tiles."""
    priority: Optional[Priority] = None
    has_data: Optional[bool] = None
    page: int = Field(1, ge=1)
    per_page: int = Field(100, ge=1, le=500)


# ============================================================================
# Response Models
# ============================================================================

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    database: str
    timestamp: datetime


class CoverageStats(BaseModel):
    """Coverage statistics for a priority level."""
    total_tiles: int
    tiles_with_data: int
    locations_total: int
    locations_checked: int
    percent_complete: float


class CurrentJobInfo(BaseModel):
    """Information about a currently running job."""
    running: bool
    job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    priority: Optional[str] = None
    tiles_processed: int = 0
    tiles_total: Optional[int] = None
    locations_updated: int = 0


class DatabaseStats(BaseModel):
    """Database statistics."""
    total_entries: int
    entries_with_dates: int
    unique_tiles: int


class StatusResponse(BaseModel):
    """System status response."""
    status: str
    last_update: Optional[datetime] = None
    next_update: Optional[datetime] = None
    coverage: Dict[str, CoverageStats]
    current_job: CurrentJobInfo
    database: DatabaseStats


class TileInfo(BaseModel):
    """Information about a single tile."""
    tile_id: str
    bbox: List[float]
    priority: str
    has_data: bool
    location_count: int
    last_updated: Optional[datetime] = None


class TilesListResponse(BaseModel):
    """Response for tile listing."""
    tiles: List[TileInfo]
    total: int
    page: int
    per_page: int
    pages: int


class TriggerUpdateResponse(BaseModel):
    """Response for triggering an update job."""
    status: str
    job_id: str
    message: str


class SchedulerConfig(BaseModel):
    """Scheduler configuration."""
    enabled: bool
    interval_hours: int
    next_run: Optional[datetime] = None


class UpdateConfig(BaseModel):
    """Update job configuration."""
    batch_size: int
    concurrency: int
    min_age_for_recheck_days: int
    overpass_delay_seconds: float
    samples_per_road: int
    adaptive_sampling: bool


class ConfigResponse(BaseModel):
    """Configuration response."""
    scheduler: SchedulerConfig
    update: UpdateConfig


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None


# ============================================================================
# GeoJSON Models
# ============================================================================

class GeoJSONPoint(BaseModel):
    """GeoJSON Point geometry."""
    type: str = "Point"
    coordinates: List[float]  # [lon, lat]


class GeoJSONLineString(BaseModel):
    """GeoJSON LineString geometry."""
    type: str = "LineString"
    coordinates: List[List[float]]  # [[lon, lat], ...]


class GeoJSONFeature(BaseModel):
    """GeoJSON Feature."""
    type: str = "Feature"
    geometry: Union[GeoJSONPoint, GeoJSONLineString, Dict]
    properties: Dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection."""
    type: str = "FeatureCollection"
    features: List[GeoJSONFeature]
    properties: Optional[Dict[str, Any]] = None


# ============================================================================
# Job Status Models
# ============================================================================

class JobRecord(BaseModel):
    """Database record for a job."""
    id: int
    job_id: str
    status: JobStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    priority_filter: Optional[str] = None
    tile_limit: Optional[int] = None
    tiles_processed: int = 0
    tiles_total: Optional[int] = None
    locations_updated: int = 0
    api_calls: int = 0
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class JobProgress(BaseModel):
    """Progress update for a running job."""
    job_id: str
    tiles_processed: int
    tiles_total: int
    locations_updated: int
    api_calls: int
    percent_complete: float
    current_tile: Optional[str] = None
