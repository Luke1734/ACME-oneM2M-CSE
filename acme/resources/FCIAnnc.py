#
#	FCIAnnc.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	FCI : Announceable variant
#

from __future__ import annotations
from .AnnouncedResource import AnnouncedResource
from .Resource import *
from Types import ResourceTypes as T, JSON
from Validator import constructPolicy, addPolicy

# Attribute policies for this resource are constructed during startup of the CSE
attributePolicies = constructPolicy([ 
	'et', 'acpi', 'lbl','daci', 'loc',
	'lnk' 
])
fcinAPolicies = constructPolicy([
])
attributePolicies =  addPolicy(attributePolicies, fcinAPolicies)
# TODO announceSyncType


class FCIAnnc(AnnouncedResource):

	# Specify the allowed child-resource types
	allowedChildResourceTypes:list[T] = [ ]


	def __init__(self, dct:JSON=None, pi:str=None, create:bool=False) -> None:
		super().__init__(T.FCIAnnc, dct, pi=pi, create=create, attributePolicies=attributePolicies)

