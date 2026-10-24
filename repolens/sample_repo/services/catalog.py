"""Product Catalog Service."""
from services.pricing import calculate_discounted_price

class CatalogService:
    def __init__(self):
        self.products = [{"id": 1, "name": "Telemetry Node", "base_price": 100}]

    def get_product(self, pid):
        for p in self.products:
            if p["id"] == pid:
                p["final_price"] = calculate_discounted_price(p["base_price"])
                return p
        return None
