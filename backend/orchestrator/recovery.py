"""Resume known durable step keys; never invent completion or resubmit unknown work."""
from .worker import Worker


class Recovery:
    def __init__(self,store,coordinator,bridge_factory):
        self.worker=Worker(store,coordinator,bridge_factory)

    def reconcile(self,run_id): return self.worker.run_until_boundary(run_id)
