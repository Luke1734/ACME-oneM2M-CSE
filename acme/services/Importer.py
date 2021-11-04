#
#	Importer.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	Entity to import various resources into the CSE. It is mainly run before 
#	the CSE is actually started.
#

from __future__ import annotations
import json, os, fnmatch, re
from typing import cast, List
from copy import deepcopy
from ..etc.Utils import findXPath, getCSE, resourceModifiedAttributes
from ..etc.Types import Announced, AttributePolicy, AttributePolicyDict, BasicType, Cardinality, RequestOptionality
from ..etc.Types import ResourceTypes as T
from ..etc.Types import BasicType as BT, Cardinality as CAR, RequestOptionality as RO, Announced as AN, JSON, JSONLIST
from ..services.Configuration import Configuration
from ..services import CSE as CSE
from ..services.Logging import Logging as L
from ..resources import Factory as Factory
from ..helpers.TextTools import removeCommentsFromJSON

# TODO Support child specialization in attribute definitionsEv

class Importer(object):

	# List of "priority" resources that must be imported first for correct CSE operation
	_firstImporters = [ 'csebase.json']

	def __init__(self) -> None:
		self.resourcePath = Configuration.get('cse.resourcesPath')
		self.macroMatch = re.compile(r"\$\{[\w.]+\}")
		self.isImporting = False
		L.isInfo and L.log('Importer initialized')


	def doImport(self) -> bool:
		"""	Perform all the imports.
		"""	
		return self.importAttributePolicies() and self.importFlexContainerPolicies() and self.importResources() and self.assignAttributePolicies()
		# return self.importAttributePolicies() and self.importFlexContainerPolicies() and self.importResources() 


	def importResources(self, path:str=None) -> bool:

		def setCSEParameters(csi:str, ri:str, rn:str) -> None:
			""" Set some values in the configuration and the CSE instance.
			"""
			if CSE.cseCsi != csi:
				L.logWarn(f'Imported CSEBase overwrites configuration. csi: {CSE.cseCsi} -> {csi}')
				CSE.cseCsi = csi
				Configuration.set('cse.csi', csi)

			if CSE.cseRi != ri:
				L.logWarn(f'Imported CSEBase overwrites configuration. ri: {CSE.cseRi} -> {ri}')
				CSE.cseRi  = ri
				Configuration.set('cse.ri', ri)

			if CSE.cseRn != rn:
				L.logWarn(f'Imported CSEBase overwrites configuration. rn: {CSE.cseRn} -> {rn}')
				CSE.cseRn  = rn
				Configuration.set('cse.rn', rn)


		countImport = 0
		countUpdate = 0

		# Only when the DB is empty else don't imports
		if CSE.dispatcher.countResources() > 0:
			L.isInfo and L.log('Resources already imported, skipping importing')
			# But we still need the CSI etc of the CSE
			if cse := getCSE().resource:
				# Set some values in the configuration and the CSE instance
				setCSEParameters(cse.csi, cse.ri, cse.rn)
				return True
			L.logErr('CSE not found')
			return False

		# Import
		if not path:
			if (path := self.resourcePath) is None:
				L.logErr('cse.resourcesPath not set')
				raise RuntimeError('cse.resourcesPath not set')
		if not os.path.exists(path):
			L.isWarn and L.logWarn(f'Import directory does not exist: {path}')
			return False

		L.isInfo and L.log(f'Importing resources from directory: {path}')
		self._prepareImporting()


		# first import the priority resources, like CSE, Admin ACP, Default ACP
		hasCSE = False
		for rn in self._firstImporters:
			fn = path + '/' + rn
			if os.path.exists(fn):
				L.isInfo and L.log(f'Importing resource: {fn}')
				resource = Factory.resourceFromDict(cast(JSON, self.readJSONFromFile(fn)), create=True, isImported=True).resource

			# Check resource creation
			if not CSE.registration.checkResourceCreation(resource, CSE.cseOriginator):
				continue
			if not (res := CSE.dispatcher.createResource(resource)).resource:
				L.logErr(f'Error during import: {res.dbg}', showStackTrace = False)
				return False
			ty = resource.ty
			if ty == T.CSEBase:
				# Set some values in the configuration and the CSE instance
				setCSEParameters(resource.csi, resource.ri, resource.rn)
				hasCSE = True
			countImport += 1


		# Check presence of CSE and at least one ACP
		if not (hasCSE):
			L.logErr('CSE and/or default ACP missing during import')
			self._finishImporting()
			return False

		# then get the filenames of all other files and sort them. Process them in order

		filenames = sorted(fnmatch.filter(os.listdir(path), '*.json'))
		for fn in filenames:
			if fn not in self._firstImporters:
				L.isInfo and L.log(f'Importing resource: {fn}')
				filename = path + '/' + fn

				# update an existing resource
				if 'update' in fn:
					dct = cast(JSON, self.readJSONFromFile(filename))
					keys = list(dct.keys())
					if len(keys) == 1 and (k := keys[0]) and 'ri' in dct[k] and (ri := dct[k]['ri']):
						if resource := CSE.dispatcher.retrieveResource(ri).resource:
							CSE.dispatcher.updateResource(resource, dct)
							countUpdate += 1
						# TODO handle error

				# create a new cresource
				else:
					# Try to get parent resource
					if not (jsn := self.readJSONFromFile(filename)):
						L.isWarn and L.logWarn(f'Error parsing file: {filename}')
						continue
					if resource := Factory.resourceFromDict(cast(JSON, jsn), create=True, isImported=True).resource:
						parentResource = None
						if pi := resource.pi:
							parentResource = CSE.dispatcher.retrieveResource(pi).resource

						# Determine originator for AE resources
						orig = resource.aei if resource.ty == T.AE else CSE.cseOriginator

						# Check resource creation
						if not CSE.registration.checkResourceCreation(resource, orig):
							continue
						
						# Add the resource
						CSE.dispatcher.createResource(resource, parentResource)
						countImport += 1
					else:
						L.isWarn and L.logWarn(f'Unknown or wrong resource in file: {fn}')

		self._finishImporting()
		L.isDebug and L.logDebug(f'Imported {countImport} resources')
		L.isDebug and L.logDebug(f'Updated  {countUpdate} resources')
		return True


	###########################################################################
	#
	#	Attribute Policies
	#


	def importFlexContainerPolicies(self, path:str=None) -> bool:
		"""	Import the attribute and hierarchy policies for flexContainer specializations.
		"""
		countFCP = 0

		# Get import path
		if not path:
			if (path := self.resourcePath) is None:
				L.logErr('cse.resourcesPath not set')
				raise RuntimeError('cse.resourcesPath not set')

		if not os.path.exists(path):
			L.isWarn and L.logWarn(f'Import directory for flexContainer policies does not exist: {path}')
			return False

		filenames = fnmatch.filter(os.listdir(path), '*.fcp')
		for fn in filenames:
			fn = os.path.join(path, fn)
			L.isInfo and L.log(f'Importing flexContainer attribute policies: {fn}')
			if os.path.exists(fn):
				if not (lst := cast(JSONLIST, self.readJSONFromFile(fn))):
					continue
				for ap in lst:
					if not (tpe := findXPath(ap, 'type')):
						L.logErr(f'Missing or empty resource type in file: {fn}')
						return False
					
					# Attributes are optional. However, add a dummy entry
					if not (attrs := findXPath(ap, 'attributes')):
						attrs = [ { "sname" : "__none__", "lname" : "__none__", "type" : "void", "car" : "01" } ]
						
					for attr in attrs:
						if not (attributePolicy := self._parseAttribute(attr, fn, tpe)):
							return False
						# Add the attribute to the additional policies structure
						try:
							if not CSE.validator.addFlexContainerAttributePolicy(attributePolicy):
								L.logErr(f'Cannot add attribute policies for attribute: {attributePolicy.sname} type: {tpe}')
								return False
							countFCP += 1
						except Exception as e:
							L.logErr(str(e))
							return False

		
		L.isDebug and L.logDebug(f'Imported {countFCP} flexContainer policies')
		return True


	def importAttributePolicies(self, path:str=None) -> bool:
		"""	Import the resource attribute policies.
		"""
		countAP = 0

		# Get import path
		if not path:
			if (path := self.resourcePath) is None:
				L.logErr('cse.resourcesPath not set')
				raise RuntimeError('cse.resourcesPath not set')

		if not os.path.exists(path):
			L.isWarn and L.logWarn(f'Import directory for attribute policies does not exist: {path}')
			return False

		filenames = fnmatch.filter(os.listdir(path), '*.ap')
		for fn in filenames:
			fn = os.path.join(path, fn)
			L.isInfo and L.log(f'Importing attribute policies: {fn}')
			if os.path.exists(fn):
				
				# Read the JSON file
				if not (attributeList := cast(JSON, self.readJSONFromFile(fn))):
					return False
				
				# go through all the attributes in that attribute definition file
				for sname in attributeList:
					AttributeDefs = attributeList[sname]
					if not AttributeDefs or not isinstance(AttributeDefs, list):
						L.logErr(f'Attribute definition must be a non-empty list for attribute: {sname} in file: {fn}', showStackTrace=False)
						return False

					# for each definition for this attribute parse it and add one or more attribute Policies
					for entry in AttributeDefs:
						if not (attributePolicy := self._parseAttribute(entry, fn, sname=sname)):
							return False
						# L.isDebug and L.logDebug(attributePolicy)
						for rtype in attributePolicy.rtypes:
							ap = deepcopy(attributePolicy)
							CSE.validator.addAttributePolicy(rtype, sname, ap)

					countAP += 1
		
		L.isDebug and L.logDebug(f'Imported {countAP} attribute policies')
		return True


	def assignAttributePolicies(self) -> bool:
		"""	Assign the imported attribute policies to each of the resources.
			This injects the imported attribute policies into all the Python Resource classes.
		"""
		L.isInfo and L.log(f'Assigning attribute policies to resource types')

		noErrors = True
		for ty in T:
			if (rc := Factory.resourceClassByType(ty)):								# Get the Python class for each Resource (only real resources)
				if hasattr(rc, '_attributes'):										# If it has attributes defined
					for sn in rc._attributes.keys():								# Then add the policies for those attributes
						if not (ap := CSE.validator.getAttributePolicy(ty, sn)):
							L.logErr(f'No attribute policy for: {str(ty)}.{sn}', showStackTrace=False)
							noErrors = False
							continue
						rc._attributes[sn] = ap
				else:
					L.logErr(f'Cannot assign attribute policies for resource class: {str(ty)}', showStackTrace=False)
					noErrors = False
					continue
				# Check for presence of _allowedChildResourceTypes attribute
				# TODO Move this to a general health check test function
				if not hasattr(rc, '_allowedChildResourceTypes'):
					L.logErr(f'Attribute "_allowedChildResourceTypes" missing for: {str(ty)}', showStackTrace=False)
					noErrors = False
					continue

		return noErrors


	def _parseAttribute(self, attr:JSON, fn:str, tpe:str=None, sname:str=None) -> AttributePolicy:
		"""	Parse a singel attribute definitions for normal as well as for flexContainer attributes.
		"""
		if not sname:
			if not (sname := findXPath(attr, 'sname')) or not isinstance(sname, str) or len(sname) == 0:
				L.logErr(f'Missing, empty, or wrong short name (sname) for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
				return None

		if not (ns := findXPath(attr, 'ns')):
			ns = 'm2m'	# default
		if not isinstance(ns, str) or not ns:
			L.logErr(f'"ns" must be a non-empty string for attribute: {sname} in file: {fn}', showStackTrace=False)
			return None
		if not tpe:
			tpe = f'{ns}:{sname}'

		if not (lname := findXPath(attr, 'lname')) or not isinstance(lname, str) or len(lname) == 0:
			L.logErr(f'Missing, empty, or wrong long name (lname) for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if not (tmp := findXPath(attr, 'type')) or not isinstance(tmp, str) or len(tmp) == 0 or not (typ := BT.to(tmp)):	# no default
			L.logErr(f'Missing, empty, or wrong type name (type): {tmp} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if not (tmp := findXPath(attr, 'car', '01')) or not isinstance(tmp, str) or len(tmp) == 0 or not (car := CAR.to(tmp, insensitive=True)):	# default car01
			L.logErr(f'Empty, or wrong cardinality (car): {tmp} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if not (tmp := findXPath(attr, 'oc', 'o')) or not isinstance(tmp, str) or len(tmp) == 0 or not (oc := RO.to(tmp, insensitive=True)):	# default O
			L.logErr(f'Empty, or wrong optionalCreate (oc): {tmp} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if not (tmp := findXPath(attr, 'ou', 'o')) or not isinstance(tmp, str) or len(tmp) == 0 or not (ou := RO.to(tmp, insensitive=True)):	# default O
			L.logErr(f'Empty, or wrong optionalUpdate (ou): {tmp} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if not (tmp := findXPath(attr, 'od', 'o')) or not isinstance(tmp, str) or len(tmp) == 0 or not (od := RO.to(tmp, insensitive=True)):	# default O
			L.logErr(f'Empty, or wrong optionalDiscovery (od): {tmp} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if not (tmp := findXPath(attr, 'annc', 'oa')) or not isinstance(tmp, str) or len(tmp) == 0 or not (annc := AN.to(tmp, insensitive=True)):	# default OA
			L.logErr(f'Empty, or wrong announcement (annc): {tmp} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
			return None

		if (rtypes := findXPath(attr, 'rtypes')):
			if not isinstance(rtypes, list):
				L.logErr(f'Empty, or wrong resourceTyoes (rtypes): {rtypes} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
				return None
			if not T.has(tuple(rtypes)):	# type: ignore[arg-type]
				L.logErr(f'"rtype" containes unknown resource type(s): {rtypes} for attribute: {tpe}:{sname} in file: {fn}', showStackTrace=False)
				return None

		ap = AttributePolicy(	type=typ,
								optionalCreate=oc,
								optionalUpdate=ou,
								optionalDiscovery=od,
								cardinality=car,
								announcement=annc,
								namespace=ns,
								lname=lname,
								sname=sname,
								tpe=tpe,
								rtypes=T.to(tuple(rtypes)) if rtypes else None 	# type:ignore[arg-type]
							)
		return ap


	def _prepareImporting(self) -> None:
		# temporarily disable access control
		self._oldacp = Configuration.get('cse.security.enableACPChecks')
		Configuration.set('cse.security.enableACPChecks', False)
		self.isImporting = True


	def replaceMacro(self, macro: str, filename: str) -> str:	# TODO move to helper
		macro = macro[2:-1]
		if (value := Configuration.get(macro)) is None:	# could be int or len == 0
			L.logErr(f'Unknown macro ${{{macro}}} in file {filename}')
			return f'*** UNKNWON MACRO : {macro} ***'
		return str(value)


	def readJSONFromFile(self, filename: str) -> JSON|JSONLIST:		# TODO move to helper
		"""	Read and parse a JSON data structure from a file `filename`. 
			Return the parsed structure, or `None` in case of an error.
		"""
		# read the file
		with open(filename) as file:
			content = file.read()
		# remove comments
		content = removeCommentsFromJSON(content).strip()
		if len(content) == 0:
			L.isWarn and L.logWarn(f'Empty file: {filename}')
			return None

		# replace macros
		items = re.findall(self.macroMatch, content)
		for item in items:
			content = content.replace(item, self.replaceMacro(item, filename))
		# Load JSON and return directly or as resource
		try:
			dct:JSON = json.loads(content)
		except json.decoder.JSONDecodeError as e:
			L.logErr(f'Error in file: {filename} - {str(e)}', showStackTrace=False)
			return None
		return dct


	def _finishImporting(self) -> None:
		Configuration.set('cse.security.enableACPChecks', self._oldacp)
		self.isImporting = False

