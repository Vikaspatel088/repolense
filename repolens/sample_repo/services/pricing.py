"""Pricing Calculation Service."""
from services.catalog import CatalogService

def calculate_discounted_price(price, discount=0.15):
    return price * (1.0 - discount)

def get_bundle_pricing(catalog_items):
    total = 0.0
    for it in catalog_items:
        total += calculate_discounted_price(it.get("price", 0))
    return total
