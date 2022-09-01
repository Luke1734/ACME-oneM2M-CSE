#
#	DATC.py
#
#	(c) 2022 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	ResourceType: mgmtObj:dataCollection
#

from ..etc.Types import AttributePolicyDict, ResourceTypes, JSON
from ..resources.MgmtObj import *


class DATC(MgmtObj):

	# Attributes and Attribute policies for this Resource Class
	# Assigned during startup in the Importer
	_attributes:AttributePolicyDict = {		
		# Common and universal attributes
		'rn': None,
		'ty': None,
		'ri': None,
		'pi': None,
		'ct': None,
		'lt': None,
		'et': None,
		'lbl': None,
		'cstn': None,
		'acpi':None,
		'at': None,
		'aa': None,
		'ast': None,
		'daci': None,
		
		# MgmtObj attributes
		'mgd': None,
		'obis': None,
		'obps': None,
		'dc': None,
		'mgs': None,
		'cmlk': None,

		# Resource attributes
		'cntp': None,
		'rpsc': None,
		'mesc': None,
		'rpil': None,
		'meil': None,
		'cmlk': None,
	}

	def __init__(self, dct:JSON = None, pi:str = None, create:bool = False) -> None:
		super().__init__(dct, pi, mgd = ResourceTypes.DATC, create = create)


	def validate(self, originator:str = None, create:bool = False, dct:JSON = None, parentResource:Resource = None) -> Result:
		L.isDebug and L.logDebug(f'Validating semanticDescriptor: {self.ri}')
		if (res := super().validate(originator, create, dct, parentResource)).status == False:
			return res

		# Test for unique occurence of either rpsc and rpil		
		rpscNew = Utils.findXPath(dct, '{*}/rpsc')
		rpilNew = Utils.findXPath(dct, '{*}/rpil')
		if (rpscNew or self.rpsc) and (rpilNew or self.rpil):
			return Result.errorResult(dbg = L.logDebug(f'rpsc and rpil shall not be set together'))

		# Test for unique occurence of either mesc and meil
		mescNew = Utils.findXPath(dct, '{*}/mesc')		
		meilNew = Utils.findXPath(dct, '{*}/meil')		
		if (mescNew or self.mesc) and (meilNew or self.meil):
			return Result.errorResult(dbg = L.logDebug(f'mesc and meil shall not be set together'))
		
		return Result.successResult()

