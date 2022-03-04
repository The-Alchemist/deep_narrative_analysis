# Query ontology class details
# To avoid passing a store name parameter, the ontology files are preloaded into an 'ontologies' database
# Called by create_event_turtle.py

import configparser as cp
import logging
import requests
import xml.etree.ElementTree as etree

from PyDictionary import PyDictionary

from database import query_database, query_ontology
from idiom_processing import get_noun_idiom
from nlp import get_synonym, get_named_entity_in_string, get_head_noun
from query_ontology_specific_classes import get_norp_emotion_or_lob
from utilities import dna_prefix, empty_string, resources_root, event_and_state_class, ontologies_database, \
    owl_thing, processed_prepositions

config = cp.RawConfigParser()
config.read(f'{resources_root}dna.config')
# Set geoname user id
geonamesUser = config.get('GeoNamesConfig', 'geonamesUser')

dictionary = PyDictionary()

explicit_plural = ('group', 'people')
part_of_group = ('member', 'group', 'citizen', 'people', 'affiliate', 'representative', 'associate', 'comrade')

domain_query_class = 'prefix : <urn:ontoinsights:dna:> SELECT ?class WHERE { ' \
                     '{ SERVICE <db://ontologies-database> { ?class rdfs:subClassOf+ :searchClass } } ' \
                     '{ { SERVICE <db://domain-database> { <keyword> rdfs:subClassOf* ?class } } UNION ' \
                     '{ SERVICE <db://ontologies-database> { <keyword> rdfs:subClassOf* ?class } } } }'

query_emotion = 'prefix : <urn:ontoinsights:dna:> SELECT ?superClass WHERE { ' \
                'keyword rdfs:subClassOf+ :EmotionalResponse }'

query_class = 'prefix : <urn:ontoinsights:dna:> SELECT ?class WHERE { ' \
              '<keyword> rdfs:subClassOf+ :searchClass . BIND("keyword" as ?class) }'

query_match = 'prefix : <urn:ontoinsights:dna:> SELECT ?class WHERE { ' \
              '{ ?class :verb_synonym ?vsyn . FILTER(?vsyn = "keyword") } UNION ' \
              '{ ?class :noun_synonym ?nsyn . FILTER(?nsyn = "keyword") } UNION ' \
              '{ ?class rdfs:label ?label . FILTER(lcase(?label) = "keyword") } }'

domain_query_event = \
    'prefix : <urn:ontoinsights:dna:> SELECT ?class ?prob WHERE { ' \
    '{ SERVICE <db://ontologies-database> { ?domain_class_type rdfs:subClassOf+ :EventAndState } } ' \
    '{ SERVICE <db://domain-database> { ?class rdfs:subClassOf* ?domain_class_type . ' \
    '{ { ?class :verb_synonym ?vsyn . FILTER(CONTAINS(?vsyn, "keyword")) . BIND(85 as ?prob) } UNION ' \
    '{ ?class :noun_synonym ?nsyn . FILTER(CONTAINS(?nsyn, "keyword")) . BIND(80 as ?prob) } UNION ' \
    '{ ?class rdfs:label ?label . FILTER(CONTAINS(lcase(?label), "keyword")) . BIND(75 as ?prob) } ' \
    'UNION { ?class :example ?ex . FILTER(CONTAINS(?ex, "keyword")) . BIND(70 as ?prob) } ' \
    'UNION { ?class :verb_synonym ?vsyn . FILTER(CONTAINS("keyword", ?vsyn)) . BIND(65 as ?prob) } ' \
    'UNION { ?class :noun_synonym ?nsyn . FILTER(CONTAINS("keyword", ?nsyn)) . BIND(60 as ?prob) } } } } ' \
    '} ORDER BY DESC(?prob)'

query_event = 'prefix : <urn:ontoinsights:dna:> SELECT ?class ?prob WHERE { ' \
              '?class rdfs:subClassOf+ :EventAndState . ' \
              '{ { ?class :verb_synonym ?vsyn . FILTER(CONTAINS(?vsyn, "keyword")) . BIND(85 as ?prob) } UNION ' \
              '{ ?class :noun_synonym ?nsyn . FILTER(CONTAINS(?nsyn, "keyword")) . BIND(80 as ?prob) } UNION ' \
              '{ ?class rdfs:label ?label . FILTER(CONTAINS(lcase(?label), "keyword")) . BIND(75 as ?prob) } ' \
              'UNION { ?class :example ?ex . FILTER(CONTAINS(?ex, "keyword")) . BIND(70 as ?prob) } ' \
              'UNION { ?class :verb_synonym ?vsyn . FILTER(CONTAINS("keyword", ?vsyn)) . BIND(65 as ?prob) } ' \
              'UNION { ?class :noun_synonym ?nsyn . FILTER(CONTAINS("keyword", ?nsyn)) . BIND(60 as ?prob) } } ' \
              '} ORDER BY DESC(?prob)'

domain_query_noun = \
    'prefix : <urn:ontoinsights:dna:> SELECT ?class ?prob WHERE { ' \
    '{ SERVICE <db://domain-database> { ?class rdfs:subClassOf* ?domain_class_type . ' \
    '{ { ?class :noun_synonym ?nsyn . FILTER(CONTAINS(?nsyn, "keyword")) . BIND(90 as ?prob) } UNION ' \
    '{ ?class rdfs:label ?label . FILTER(CONTAINS(lcase(?label), "keyword")) . BIND(85 as ?prob) } UNION ' \
    '{ ?class :example ?ex . FILTER(CONTAINS(?ex, "keyword")) . BIND(80 as ?prob) } UNION ' \
    '{ ?class :noun_synonym ?nsyn . FILTER(CONTAINS("keyword", ?nsyn)) . BIND(65 as ?prob) } } } } ' \
    '{ SERVICE <db://ontologies-database> { ?domain_class_type a owl:Class . ' \
    'FILTER NOT EXISTS { ?domain_class_type rdfs:subClassOf+ :EventAndState } ' \
    'FILTER NOT EXISTS { ?domain_class_type rdfs:subClassOf+ :Ethnicity } ' \
    'FILTER NOT EXISTS { ?domain_class_type rdfs:subClassOf+ :Enumeration } } } ' \
    '} ORDER BY DESC(?prob)'

query_noun = 'prefix : <urn:ontoinsights:dna:> SELECT ?class ?prob WHERE ' \
             '{ ?class a owl:Class ' \
             '{ { ?class :noun_synonym ?nsyn . FILTER(CONTAINS(?nsyn, "keyword")) . BIND(90 as ?prob) } UNION ' \
             '{ ?class rdfs:label ?label . FILTER(CONTAINS(lcase(?label), "keyword")) . BIND(85 as ?prob) } ' \
             'UNION { ?class :example ?ex . FILTER(CONTAINS(?ex, "keyword")) . BIND(80 as ?prob) } ' \
             'UNION { ?class :noun_synonym ?nsyn . FILTER(CONTAINS("keyword", ?nsyn)) . BIND(65 as ?prob) } } ' \
             'FILTER NOT EXISTS { ?class rdfs:subClassOf+ :EventAndState } ' \
             'FILTER NOT EXISTS { ?class rdfs:subClassOf+ :Ethnicity } ' \
             'FILTER NOT EXISTS { ?class rdfs:subClassOf+ :Enumeration } } ORDER BY DESC(?prob)'


def create_affiliation_ttl(noun_uri: str, noun_text: str, affiliated_text: str, affiliated_type: str) -> list:
    """
    Creates the Turtle for an Affiliation.

    :param noun_uri: String holding the entity/URI to be affiliated
    :param noun_text: String holding the sentence text for the entity
    :param affiliated_text: String specifying the entity (organization, group, etc.) to which the
                            noun is affiliated
    :param affiliated_type: String specifying the class type of the entity
    :returns: An array of strings holding the Turtle representation of the Affiliation
    """
    affiliated_uri = f':{affiliated_text.replace(" ", "_")}'
    affiliation_uri = f'{noun_uri}{affiliated_text.replace(" ", "_")}Affiliation'
    noun_str = f"'{noun_text}'"
    ttl = [f'{affiliation_uri} a :Affiliation ; :affiliated_with {affiliated_uri} ; :affiliated_agent {noun_uri} .',
           f'{affiliation_uri} rdfs:label "Relationship based on the text, {noun_str}" .',
           f'{affiliated_uri} a {affiliated_type} ; rdfs:label "{affiliated_text}" .']
    wikidata_desc = get_wikipedia_description(affiliated_text)
    if wikidata_desc:
        ttl.append(f'{affiliated_uri} :definition "{wikidata_desc}" .')
    return ttl


def check_dictionary(word: str, is_noun: bool) -> str:
    """
    Check dictionary for an unmapped term and see if a mapping can be created.

    :param word: The term to be mapped
    :param is_noun: Boolean indicating if the word/term should be checked as a noun first
    :returns: String holding the concept's class name
    """
    if ' ' in word:
        word = get_head_noun(word)[0]  # In this case, we don't care about plurals, etc. (the orig_text)
    try:
        word_def = dictionary.meaning(word)
        if word_def:
            term_types = ('Verb', 'Noun')
            if is_noun:
                term_types = ('Noun', 'Verb')
            for term_type in term_types:
                if term_type in word_def.keys():
                    for i, clause in enumerate(word_def[term_type]):
                        if i > 3:   # TODO: Is 3 the right number of definitions to check?
                            break
                        for sub_clause in clause.split(';'):
                            if term_type == 'Noun':
                                word = get_head_noun(sub_clause)[0]
                                query_str = query_noun
                                domain_query_str = domain_query_noun
                            else:
                                word = sub_clause.split(' ')[0]
                                query_str = query_event
                                domain_query_str = domain_query_event
                            # First check for exact match
                            class_name = query_ontology(word, query_match, query_match)
                            if class_name != owl_thing:
                                return class_name
                            class_name = query_ontology(word, query_str, domain_query_str)
                            if class_name != owl_thing:
                                return class_name
        return owl_thing
    except Exception as e:
        logging.error(f'Exception when getting noun class for {word}: {e}')
        return owl_thing


def check_emotion(class_name: str) -> bool:
    """
    Determines if the class_name is a positive or negative emotion, or not an emotion.

    :param class_name: The class_name to be analyzed
    :returns: Boolean indicating that the class is a type of EmotionalResponse (true) or not (false)
    """
    results = query_database('select', query_emotion.replace('keyword', class_name), ontologies_database)
    if results:
        return True
    return False


def check_presence_of_words(text: str, words: tuple) -> bool:
    """
    Determines if one of the 'words' in the input tuple is found in the 'text' string. Returns
    True if so.

    :param text: The string to be checked
    :param words: A tuple holding words to be searched for
    :returns: True if one of the 'words' is found in the 'text; False otherwise
    """
    for word in words:
        if word in text:
            return True
    return False


def get_event_state_class(lemma: str) -> str:
    """
    Determine the appropriate event or state in the DNA ontology, that matches the semantics of
    the verb.

    :param lemma: The verb lemma or noun indicating an action/event
    :returns: Mapping of the verb/sentence semantics to the DNA ontology, returning the class name
    """
    # First check for an exact match
    class_name = query_ontology(lemma, query_match, query_match)
    if class_name == owl_thing:
        class_name = query_ontology(lemma, query_event, domain_query_event)
        if class_name == owl_thing:
            # Applicable class not found, check dictionary and synonyms
            class_name = check_dictionary(lemma, False)   # False = Not a noun
            if class_name == owl_thing:
                logging.warning(f'Could not map the verb, {lemma}, to the ontology')
    # Check if this is a movement/travel/transportation event
    if class_name != owl_thing and _indicate_location_or_movement(class_name, True):
        class_name = f'{class_name}>, <{dna_prefix}MovementTravelAndTransportation'
    if class_name == owl_thing:   # Simplified return from _query_ontology but need to correct for a verb
        class_name = event_and_state_class
    return class_name.replace('urn:ontoinsights:dna', empty_string).replace('>', empty_string).\
        replace('<', empty_string)


def get_geonames_location(loc_text: str) -> (str, str, int):
    """
    Get the type of location from its text as well as its country and administrative level (if relevant).

    :param loc_text: Location text
    :returns: A tuple holding the location's class type, country name (or an empty string or None),
             and an administrative level (if 0, then admin level is not applicable) or GeoNames ID
             (for a Country)
    """
    logging.info(f'Getting geonames details for {loc_text}')
    # TODO: Need to add sleep to meet geonames timing requirements?
    response = requests.get(
        f'http://api.geonames.org/search?q={loc_text.lower().replace(" ", "+")}&maxRows=1&username={geonamesUser}')
    root = etree.fromstring(response.content)
    country = _get_xml_value('./geoname/countryName', root)
    feature = _get_xml_value('./geoname/fcl', root)
    fcode = _get_xml_value('./geoname/fcode', root)
    if not country and not feature and not fcode:
        return empty_string, empty_string, 0
    # Defaults
    admin_level = 0
    class_type = ":PopulatedPlace"
    if feature == 'A':     # Administrative area
        if fcode.startswith('ADM'):
            class_type += ', :AdministrativeDivision'
            # Get administrative level
            if any([i for i in fcode if i.isdigit()]):
                admin_level = [int(i) for i in fcode if i.isdigit()][0]
        elif fcode.startswith('PCL'):  # Some kind of political entity, assuming for now that it is a country
            class_type = ":Country"
            country = empty_string
        elif fcode.startswith('L') or fcode.startswith('Z'):
            # Leased area usually for military installations or a zone, such as a demilitarized zone
            return empty_string, empty_string, admin_level
    elif feature == 'P':   # Populated Place
        if fcode.startswith('PPLA'):
            class_type += ', :AdministrativeDivision'
            # Get administrative level
            admin_levels = [int(i) for i in fcode if i.isdigit()]
            if admin_levels:
                admin_level = admin_levels[0]
            else:
                admin_level = 1
    return class_type, country, admin_level


def get_noun_ttl(noun_uri: str, noun_text: str, noun_type: str, sentence_text: str) -> list:
    """
    Determine the appropriate class in the DNA ontology, that matches the semantics of the noun_text.
    First check for people, locations/GPEs, ethnicities, religions, etc., then for idioms and lastly
    for word matches, definition matches and event/state class matches.

    :param noun_uri: String identifying the URI/URL/individual associated with the noun_text
    :param noun_text: String specifying the noun text from the original narrative sentence
    :param noun_type: String holding the type of the noun (e.g., 'FEMALESINGPERSON' or 'PLURALNOUN')
    :param sentence_text: The full text of the sentence (needed for checking for idioms)
    :returns: An array of the resulting Turtle for a mapping of the semantics to a DNA ontology class
    """
    class_name = empty_string
    noun = empty_string
    wikipedia_desc = empty_string
    # TODO: Update logic if more than Countries are defined in the core DNA ontologies
    if noun_type.endswith('GPE') or noun_type.endswith('ORG'):
        wikipedia_desc = get_wikipedia_description(noun_text)
    if 'PERSON' in noun_type or noun_text == 'Narrator':
        class_name = 'urn:ontoinsights:dna:Person'
        if 'PLURAL' in noun_type:
            class_name = 'urn:ontoinsights:dna:Person>, <urn:ontoinsights:dna:Collection'
    elif noun_type.endswith('GPE'):
        class_name = 'urn:ontoinsights:dna:GeopoliticalEntity'
    elif noun_type.endswith('NORP'):   # Nationalities, religious or political groups
        return _process_norp_aspect(noun_text, noun_type, noun_uri, wikipedia_desc)
    elif noun_type.endswith('ORG'):
        class_name = 'urn:ontoinsights:dna:Organization'
    else:
        # Could be a reference to 'members OF'/'people IN'/... an org, group, ...
        found_prep = False
        for prep in processed_prepositions:
            found_prep = (True if noun_text.lower().startswith(f'{prep} ') else False) or \
                         (True if f' {prep} ' in noun_text.lower() else False)
            if found_prep:
                break
        if found_prep:
            if check_presence_of_words(noun_text, part_of_group):
                if 'PLURAL' in noun_type or check_presence_of_words(noun_text, explicit_plural):
                    class_name = 'urn:ontoinsights:dna:Person>, <urn:ontoinsights:dna:Collection'
                else:
                    class_name = 'urn:ontoinsights:dna:Person'
                # Check if the org, group, ... is mentioned
                if ' of ' in noun_text or ' in ' in noun_text or ' from ' in noun_text:
                    # TODO: Handle general group reference (ex: 'soldiers in the Soviet army')
                    agent_text, agent_type = get_named_entity_in_string(noun_text)
                    if agent_text:    # Found a named entity that is an affiliated agent
                        new_ttl = create_affiliation_ttl(noun_uri, noun_text, agent_text, agent_type)
                        new_ttl.append(f'{noun_uri} a <{class_name}> ; rdfs:label "{noun_text}" .')
                        return new_ttl
            else:
                new_ttl = _check_for_noun_idiom(noun_text, noun_type, sentence_text, noun_uri)
                if new_ttl:
                    return new_ttl
                noun = get_head_noun(noun_text)[0]
        else:
            new_ttl = _check_for_noun_idiom(noun_text, noun_type, sentence_text, noun_uri)
            if new_ttl:
                return new_ttl
            noun = get_head_noun(noun_text)[0]
    if not noun:
        noun = noun_text
    if not class_name:
        class_name = _get_noun_class(noun)
    if class_name == owl_thing:
        logging.warning(f'Could not map the noun, {noun_text}, to the ontology')
    if class_name != owl_thing and _indicate_location_or_movement(class_name, False):
        class_name = f'{class_name}>, <{dna_prefix}Location'
    if 'PLURAL' in noun_type and 'PERSON' not in noun_type:
        class_name = f'{class_name}>, <urn:ontoinsights:dna:Collection'
    ttl_list = [f'{noun_uri} a <{class_name}> ; rdfs:label "{noun_text}" .']
    if noun_type.startswith('NEG'):
        ttl_list.append(f'{noun_uri} :negation true .')
    if wikipedia_desc:
        ttl_list.append(f'{noun_uri} :description "{wikipedia_desc}" .')
    return ttl_list


def get_wikipedia_description(noun: str) -> str:
    """
    Get the first paragraph of the Wikipedia web page for the specified organization, group, ...

    :param noun: String holding the organization/group name
    :returns: String that is the first paragraph of the Wikipedia page (if the org/group is found);
            otherwise, an empty string
    """
    logging.info(f'Getting wikipedia details for {noun}')
    resp = requests.get(f'https://en.wikipedia.org/api/rest_v1/page/summary/{noun.replace(" ", "_")}')
    wikipedia = resp.json()
    if "extract" in wikipedia.keys():
        wiki_text = wikipedia['extract'].replace('"', "'").replace('\xa0', ' ').\
            encode('ASCII', errors='replace').decode('utf-8')
        wiki_text = f"'{wiki_text}'"
        return f'From Wikipedia (wikibase_item: {wikipedia["wikibase_item"]}): {wiki_text}'
    return empty_string


# Functions internal to the module
def _check_for_noun_idiom(noun_text: str, noun_type: str, sentence_text: str, noun_uri: str) -> list:
    """
    Processing to determine if a "noun" idiom has been defined.

    :param noun_text: String holding the noun text
    :param noun_type: String holding the type of the noun (e.g., 'FEMALESINGPERSON' or 'PLURALNOUN')
    :param sentence_text: The full text of the sentence (needed for checking for some idioms)
    :param noun_uri: String identifying the noun concept as a URI/IRI
    :returns: An array holding the Turtle statements that are generated if a noun idiom is found
            (or an empty list otherwise)
    """
    noun, orig_text = get_head_noun(noun_text)
    noun_ttl = get_noun_idiom(noun, noun_text, noun_type, sentence_text, noun_uri)
    if not noun_ttl:
        # The mapping may be specific to a plural
        noun_ttl = get_noun_idiom(orig_text, noun_text, noun_type, sentence_text, noun_uri)
    if noun_ttl:
        return noun_ttl
    noun_syns = get_synonym(noun, True)
    for noun_syn in noun_syns:
        noun_ttl = get_noun_idiom(noun_syn, noun_text, noun_type, sentence_text, noun_uri)
        if noun_ttl:
            return noun_ttl
    return []


def _get_noun_class(noun_text: str) -> str:
    """
    Query the ontology to determine the appropriate class in the DNA ontology, that matches the
    semantics of the noun_text. Check for word matches, definition matches and event/state class matches.

    :param noun_text: String holding the text to be matched
    :returns: The class name that matches the semantics of the noun, or a reference to owl:Thing
    """
    # First check for an exact match
    class_name = query_ontology(noun_text, query_match, query_match)
    if class_name == owl_thing:
        class_name = query_ontology(noun_text, query_noun, domain_query_noun)
        if class_name == owl_thing:   # Nothing found for the word as a noun
            words = get_synonym(noun_text, True)   # Get synonyms
            for word in words:
                class_name = query_ontology(word, query_noun, domain_query_noun)
                if class_name == owl_thing:
                    # Check for event/state
                    class_name = query_ontology(word, query_event, domain_query_event)
                if class_name != owl_thing:
                    break
        if class_name == owl_thing:   # Nothing found for the word synonyms
            # Check the dictionary for alternate terms
            class_name = check_dictionary(noun_text, True)    # True = Check nouns first
    return class_name


def _get_xml_value(xpath: str, root: etree.Element) -> str:
    """
    Use the input xpath string to access specific elements in an XML tree.

    :param xpath: String identifying the path to the element to be retrieved
    :param root: The root element of the XML tree
    :returns: String representing the value of the specified element or an empty string
             (if not defined)
    """
    elems = root.findall(xpath)
    if elems:
        return elems[0].text
    else:
        return empty_string


def _indicate_location_or_movement(class_name: str, for_movement: bool) -> bool:
    """
    Check if the event/class_name is a subclass of :MovementTravelAndTransportation.

    :param class_name: The type of event as defined by its class name
    :param for_movement: Boolean indicating that the query is for subclasses of
                         :MovementTravelAndTransportation if true, and for subclasses of :Location
                         if false
    :returns: Boolean of True if a subclass of :MovementTravelAndTransportation or :Location
             (depending on the value of the for_movement boolean) and False otherwise
    """
    if ', ' in class_name:
        return False
    query_str = query_class.replace('searchClass', 'Location')
    domain_query_str = domain_query_class.replace('searchClass', 'Location')
    if for_movement:
        query_str = query_class.replace('searchClass', 'MovementTravelAndTransportation')
        domain_query_str = domain_query_class.replace('searchClass', 'MovementTravelAndTransportation')
    result = query_ontology(class_name, query_str, domain_query_str)
    if result == owl_thing:
        return False
    return True


def _process_norp_aspect(noun_text: str, noun_type: str, noun_uri: str, wikipedia_desc: str) -> (str, list):
    """
    Definition of the Turtle for a Person, Group or Organization that was identified by SpaCy as a
    'NORP' (nationality, religion, political party, ...).

    :param noun_text: String holding the noun text
    :param noun_type: String holding the type of the noun (e.g., 'FEMALESINGPERSON' or 'PLURALNOUN')
    :param noun_uri: String identifying the noun concept as a URI/IRI
    :param wikipedia_desc: String holding the definition/description of the noun from Wikipedia
    :returns: A tuple holding the type of noun (e.g., Person or GroupOfAgents) and an array of
             the defining Turtle (if appropriate)
    """
    # Check if ethnic group, religious group, ... that is already defined in the DNA ontology
    norp_type, norp_class = get_norp_emotion_or_lob(noun_text)   # TODO: Optimize to not check for emotion or lob
    if not norp_type or norp_type == 'EmotionalResponse' or norp_type == 'LineOfBusiness':
        # Check if a type of ethnic, religious or political group by checking the Wikipedia description
        if wikipedia_desc:
            wikipedia_lower = wikipedia_desc.lower()
            if 'political' in wikipedia_lower or 'religio' in wikipedia_lower or 'ethnic' in wikipedia_lower:
                words = wikipedia_desc.split(' ')
                for word in words:
                    if not word.istitle():   # Word is likely capitalized, so ignore lower cased words
                        continue
                    norp_type, norp_class = get_norp_emotion_or_lob(word)
                    if norp_type and norp_type != 'EmotionalResponse' and norp_type != 'LineOfBusiness':
                        break
    # Did not find match of the word or its description
    if not norp_type or norp_type == 'EmotionalResponse' or norp_type == 'LineOfBusiness':
        type_str = ':Organization'
        if 'PLURAL' in noun_type:
            type_str = ':Organization, :Collection'
        norp_ttl = [f'{noun_uri} a <{type_str}> ; rdfs:label "{noun_text}" .']
        if wikipedia_desc:
            norp_ttl.append(f'{noun_uri} :description "{wikipedia_desc}" .')
    else:
        type_str = ':Person'
        if 'PLURAL' in noun_type:
            type_str = ':Person, :Collection'
        norp_ttl = [f'{noun_uri} a {type_str} ; rdfs:label "{noun_text}" .']
        if norp_type == 'ReligiousBelief' or norp_type == 'Ethnicity':
            norp_ttl.append(f'{noun_uri} :has_agent_aspect <{norp_class}> .')
        elif norp_type == 'PoliticalIdeology':
            norp_ttl.append(f'{noun_uri} :has_political_ideology <{norp_class}> .')
    if noun_type.startswith('NEG'):
        norp_ttl.append(f'{noun_uri} :negation true .')
    return norp_ttl
