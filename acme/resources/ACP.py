#
#	ACP.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	ResourceType: AccessControlPolicy
#

from Constants import Constants as C
from .Resource import *
import Utils


class ACP(Resource):

	def __init__(self, jsn=None, pi=None, create=False):
		super().__init__(C.tsACP, jsn, pi, C.tACP, create=create, inheritACP=True)

		# store permissions for easier access
		self._storePermissions()


	def validate(self, originator):
		if (res := super().validate(originator))[0] == False:
			return res
		self._storePermissions()
		return (True, C.rcOK)


	def checkPermission(self, origin, requestedPermission):
		if requestedPermission & self.pv_acop == 0:	# permission not fitting at all
			return False
		return 'all' in self.pv_acor or origin in self.pv_acor or requestedPermission == C.permNOTIFY


	def checkSelfPermission(self, origin, requestedPermission):
		if requestedPermission & self.pvs_acop == 0:	# permission not fitting at all
			return False
		return 'all' in self.pvs_acor or origin in self.pvs_acor


	def _storePermissions(self):
		self.pv_acop = self.attribute('pv/acr/acop', 0)
		self.pv_acor = self.attribute('pv/acr/acor', [])
		self.pvs_acop = self.attribute('pvs/acr/acop', 0)
		self.pvs_acor = self.attribute('pvs/acr/acor', [])

