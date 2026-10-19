"""pdfgeom: helpers that let an agent turn building-plan PDFs into EnergyPlus geometry (epJSON).

pdfkit reads the plans, model loads building.json, geometry builds typed 3D surfaces, qa checks the
takeoff, viewer draws it and epjson writes it as a minimal EnergyPlus model.
"""

__version__ = "0.1.0"
