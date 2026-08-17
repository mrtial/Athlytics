def normalize_activity_type(type_key: str | None) -> str:
    """Normalize raw provider sport type / typeKey into canonical category.

    Shared across providers (Garmin, Strava, ...) so activity_type values
    are comparable cross-source -- this is what lets
    core.storage.repository.upsert_activities match a Garmin activity
    against its Strava mirror by activity_type during dedup.
    """
    if not type_key:
        return "other"
    tk = str(type_key).lower().replace("-", "_")
    if any(r in tk for r in ("running", "run", "jogging", "treadmill")):
        return "running"
    if any(c in tk for c in ("cycling", "biking", "bike", "ride", "cycl")):
        return "cycling"
    if any(s in tk for s in ("swimming", "swim", "pool", "lap")):
        return "swimming"
    if any(w in tk for w in ("walking", "walk")):
        return "walking"
    if any(h in tk for h in ("hiking", "hike")):
        return "hiking"
    if any(st in tk for st in ("strength", "weight", "gym")):
        return "strength_training"
    if any(cd in tk for cd in ("cardio", "elliptical", "stair", "hiit", "rowing", "rower", "fitness_equipment")):
        return "cardio"
    if any(y in tk for y in ("yoga", "pilates", "stretch")):
        return "yoga"
    return tk
