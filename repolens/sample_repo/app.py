"""RepoLens Demo Application - E-Commerce Service."""
import sys
from services.auth import AuthService
from services.catalog import CatalogService

def run():
    auth = AuthService()
    catalog = CatalogService()
    print("RepoLens demo server running.")

if __name__ == '__main__':
    run()
