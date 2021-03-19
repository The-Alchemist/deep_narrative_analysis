# Deep Narrative Analysis (DNA)

## License
Creative Commons 
Atribution 4.0 International 
CC BY 4.0

## Overview 

Deep Narrative Analysis (DNA) is a toolset for analyzing narratives. It combines semantic and Python technologies with machine learning to 1) create knowledge graphs encoding the details of the narratives, 2) create knowledge graphs encoding background/contextual knowledge from online and structured data sources, and 3) perform inference, reasoning and statistical analyses. 

## File Structure

The semantics (ontologies) and processing (initially, Jupyter notebooks) are captured in the directories of this project. The following folder structure is used:

* _ontologies_ holds the definitions of the concepts that will be extracted from the narratives and online/structured data, and encoded as knowledge graphs
  * Some of the ontology files have been updated to align with March 2021 redesign
  * This work will be complete by the end of March 2021
* _ontol-docs_ contains documentation explaining the ontologies and their usage
  * The _graphs_ sub-directory contains PNGs of the main ontology concepts
* _notebooks_ holds the Jupyter notebooks used to scrape/parse web pages, and perform initial parsing and analysis experiments on the narratives
  * Note that various .config files are also defined to customize the processing
