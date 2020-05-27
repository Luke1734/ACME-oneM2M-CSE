#
#	SecurityManager.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	This entity handles access to resources
#


from Logging import Logging
from Constants import Constants as C
import CSE
from Configuration import Configuration

# Mapping between request operations and permissions
operationsPermissions =	{ C.opRETRIEVE	: C.permRETRIEVE,
						  C.opCREATE 	: C.permCREATE,
						  C.opUPDATE 	: C.permUPDATE,
		  				  C.opDELETE 	: C.permDELETE
						}

class SecurityManager(object):

	def __init__(self):
		Logging.log('SecurityManager initialized')
		if Configuration.get('cse.enableACPChecks'):
			Logging.log('ACP checking ENABLED')
		else:
			Logging.log('ACP checking DISABLED')


	def shutdown(self):
		Logging.log('SecurityManager shut down')


	def hasAccess(self, originator, resource, requestedPermission, checkSelf=False, ty=None, isCreateRequest=False, parentResource=None):
		if not Configuration.get('cse.enableACPChecks'):	# check or ignore the check
			return True

		# originator may be None or empty or C or S. 
		# That is okay if type is AE and this is a create request
		if originator is None or len(originator) == 0 or originator in ['C', 'S']:
			if ty is not None and ty == C.tAE and isCreateRequest:
				Logging.logDebug("Empty originator for AE CREATE. OK.")
				return True

		# Check parameters
		if resource is None:
			Logging.logWarn("Resource must not be None")
			return False
		if requestedPermission is None or not (0 <= requestedPermission <= C.permALL):
			Logging.logWarn("RequestedPermission must not be None, and between 0 and 63")
			return False

		Logging.logDebug("Checking permission for originator: %s, ri: %s, permission: %d, selfPrivileges: %r" % (originator, resource.ri, requestedPermission, checkSelf))


		if resource.ty == C.tGRP: # target is an group resource
			# Check membersAccessControlPolicyIDs if provided, otherwise accessControlPolicyIDs to be used
			
			if (macp := resource.macp) is None or len(macp) == 0:
				Logging.logDebug("MembersAccessControlPolicyIDs not provided, using AccessControlPolicyIDs")
				# FALLTHROUGH to the permission checks below
			
			else: # handle the permission checks here
				for a in macp:
					(acp, _) = CSE.dispatcher.retrieveResource(a)
					if acp is None:
						continue
					else:
						if acp.checkPermission(originator, requestedPermission):
							Logging.logDebug('Permission granted')
							return True
				Logging.logDebug('Permission NOT granted')
				return False


		if resource.ty == C.tACP:	# target is an ACP resource
			if resource.checkSelfPermission(originator, requestedPermission):
				Logging.logDebug('Permission granted')
				return True

		else:		# target is any other resource type
			
			# If subscription, check whether originator has retrieve permissions on the subscribed-to resource (parent)	
			if resource.ty == C.tSUB and parentResource is not None:
				if self.hasAccess(originator, parentResource, C.permRETRIEVE) == False:
					return (None, C.rcOriginatorHasNoPrivilege)

			if (acpi := resource.acpi) is None or len(acpi) == 0:	
				if resource.inheritACP:
					(parentResource, _) = CSE.dispatcher.retrieveResource(resource.pi)
					return self.hasAccess(originator, parentResource, requestedPermission, checkSelf)
				Logging.logDebug("Missing acpi in resource")
				return False

			for a in acpi:
				(acp, _) = CSE.dispatcher.retrieveResource(a)
				if acp is None:
					continue
				if checkSelf:	# forced check for self permissions
					if acp.checkSelfPermission(originator, requestedPermission):
						Logging.logDebug('Permission granted')
						return True				
				else:
					if acp.checkPermission(originator, requestedPermission):
						Logging.logDebug('Permission granted')
						return True

		# no fitting permission identified
		Logging.logDebug('Permission NOT granted')
		return False

