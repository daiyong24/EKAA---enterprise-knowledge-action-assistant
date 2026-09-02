"""Static deployment configuration safety checks."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    environment: str
    database_url: str
    debug: bool
    release_migration_job_enabled: bool
    app_startup_migrations_enabled: bool
    tls_termination: str

    def validate(self) -> None:
        if self.environment not in {"development", "staging", "production"}:
            raise ValueError("deployment environment is not supported")
        if self.environment == "production" and self.debug:
            raise ValueError("production deployment cannot enable debug")
        if self.environment in {
            "staging",
            "production",
        } and not self.database_url.startswith("postgresql"):
            raise ValueError("shared deployment requires PostgreSQL")
        if self.environment == "production" and self.tls_termination != "load_balancer":
            raise ValueError("production TLS must terminate at the load balancer")
        if self.environment == "production" and not self.release_migration_job_enabled:
            raise ValueError("production release must run an explicit migration job")
        if (
            self.environment in {"staging", "production"}
            and self.app_startup_migrations_enabled
        ):
            raise ValueError("shared deployments must disable app-startup migrations")
