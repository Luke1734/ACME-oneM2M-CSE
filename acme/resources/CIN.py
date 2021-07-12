#
#	CIN.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	ResourceType: ContentInstance
#

from Constants import Constants as C
from Types import ResourceTypes as T, Result, ResponseCode as RC, JSON
from Validator import constructPolicy, addPolicy
from .Resource import *
from .AnnounceableResource import AnnounceableResource

# Attribute policies for this resource are constructed during startup of the CSE
attributePolicies = constructPolicy([ 
	'rn', 'ty', 'ri', 'pi', 'et', 'ct', 'lt', 'st', 'lbl', 'at', 'aa', 'cr',
])
cinPolicies = constructPolicy([
	'cnf', 'cs', 'conr', 'con', 'or', 'conr'
])
attributePolicies = addPolicy(attributePolicies, cinPolicies)


class CIN(AnnounceableResource):

	def __init__(self, dct:JSON=None, pi:str=None, create:bool=False) -> None:
		super().__init__(T.CIN, dct, pi, create=create, inheritACP=True, readOnly = True, attributePolicies=attributePolicies)

		self.resourceAttributePolicies = cinPolicies	# only the resource type's own policies

		if self.dict is not None:
			self.setAttribute('con', '', overwrite=False)
			self.setAttribute('cs', Utils.getAttributeSize(self.con))
			# if isinstance(self.con, str):
			# 	self.setAttribute('cs', Utils.getAttributeSize(self.con))
			# else:
			# 	self.setAttribute('cs', 0)



	# Enable check for allowed sub-resources. No Child for CIN
	def canHaveChild(self, resource:Resource) -> bool:
		return super()._canHaveChild(resource, [])


	# Forbid updating
	def update(self, dct:JSON=None, originator:str=None) -> Result:
		return Result(status=False, rsc=RC.operationNotAllowed, dbg='updating CIN is forbidden')


	def willBeRetrieved(self) -> Result:
		if not (res := super().willBeRetrieved()).status:
			return res

		# Check whether the parent container's *disableRetrieval* attribute is set to True.
		if (cnt := self.retrieveParentResource()) is not None and (disr := cnt.disr) is not None and disr:	# False means "not disabled retrieval"
			L.isDebug and L.logDebug(dbg := f'Retrieval is disabled for the parent <container>')
			return Result(status=False, rsc=RC.operationNotAllowed, dbg=dbg)	

		return Result(status=True)


	def validate(self, originator:str=None, create:bool=False, dct:JSON=None, parentResource:Resource=None) -> Result:
		if (res := super().validate(originator, create, dct, parentResource)).status == False:
			return res

		# Check the format of the CNF attribute
		if (cnf := self.cnf) is not None:
			if not (res := CSE.validator.validateCNF(cnf)).status:
				return Result(status=False, rsc=RC.badRequest, dbg=res.dbg)

		# Add ST attribute
		if (parentResource := parentResource.dbReload().resource) is not None:		# Read the resource again in case it was updated in the DB
			self.setAttribute('st', parentResource.st)
		
		return Result(status=True)

