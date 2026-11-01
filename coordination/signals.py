def seed_service_catalog(sender, **kwargs):
    Service = sender.get_model("Service")
    catalog = [
        "General construction", "Masonry", "Carpentry", "Electrical installation",
        "Plumbing", "Welding", "Painting", "Tiling", "Steel fixing", "Roofing",
        "Heavy equipment operation", "Surveying",
    ]
    for name in catalog:
        Service.objects.get_or_create(name=name)
