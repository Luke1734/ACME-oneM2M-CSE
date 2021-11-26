#
#	NotificationManager.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	This entity handles subscriptions and sending of notifications. 
#

from __future__ import annotations
import isodate
from typing import Optional, Callable, Tuple, Union
from threading import Lock
from tinydb.utils import V

from ..etc.Constants import Constants as C
from ..etc.Types import ContentSerializationType, MissingData, Result, NotificationContentType, NotificationEventType, ResponseStatusCode as RC
from ..etc.Types import JSON, Parameters
from ..etc import Utils as Utils
from ..services.Logging import Logging as L
from ..services.Configuration import Configuration
from ..services import CSE as CSE
from ..resources.Resource import Resource
from ..helpers.BackgroundWorker import BackgroundWorkerPool

# TODO: removal policy (e.g. unsuccessful tries)

SenderFunction = Callable[[str], bool]	# type:ignore[misc] # bc cyclic definition 
""" Type definition for sender callback function. """


class NotificationManager(object):


	def __init__(self) -> None:
		self.lockBatchNotification = Lock()	# Lock for batchNotifications
		if L.isInfo: L.log('NotificationManager initialized')


	def shutdown(self) -> bool:
		L.isInfo and L.log('NotificationManager shut down')
		return True

	###########################################################################
	#
	#	Subscriptions
	#

	def addSubscription(self, subscription:Resource, originator:str) -> Result:
		"""	Add a new subscription. Check each receipient with verification requests. """
		L.isDebug and L.logDebug('Adding subscription')
		if not (res := self._verifyNusInSubscription(subscription, originator = originator)).status:	# verification requests happen here
			return Result(status=False, rsc=res.rsc, dbg=res.dbg)
		return Result(status=True) if CSE.storage.addSubscription(subscription) else Result(status = False, rsc = RC.internalServerError, dbg = 'cannot add subscription to database')


	def removeSubscription(self, subscription:Resource) -> Result:
		""" Remove a subscription. Send the deletion notifications, if possible. """
		L.isDebug and L.logDebug('Removing subscription')

		# Send outstanding batchNotifications for a subscription
		self._flushBatchNotifications(subscription)

		# Send a deletion request to the subscriberURI
		if not self._sendDeletionNotification(su := subscription['su'], subscription.ri):
			L.isDebug and L.logDebug(f'Deletion request failed for: {su}') # but ignore the error

		# Send a deletion request to the associatedCrossResourceSub
		if (acrs := subscription['acrs']):
			self._sendDeletionNotification([ nu for nu in acrs ], subscription.ri)
		
		# Finally remove subscriptions from storage
		return Result(status=True) if CSE.storage.removeSubscription(subscription) else Result(status=False, rsc=RC.internalServerError, dbg='cannot remove subscription from database')


	def updateSubscription(self, subscription:Resource, previousNus:list[str], originator:str) -> Result:
		L.isDebug and L.logDebug('Updating subscription')
		if not (res := self._verifyNusInSubscription(subscription, previousNus, originator=originator)).status:	# verification requests happen here
			return Result(status=False, rsc=res.rsc, dbg=res.dbg)
		return Result(status=True) if CSE.storage.updateSubscription(subscription) else Result(status=False, rsc=RC.internalServerError, dbg='cannot update subscription in database')


	def checkSubscriptions(self, resource:Resource, reason:NotificationEventType, childResource:Resource=None, modifiedAttributes:JSON=None, ri:str=None, missingData:dict[str, MissingData]=None, now:float=None) -> None:
		if Utils.isVirtualResource(resource):
			return 
		ri = resource.ri if not ri else ri
		L.isDebug and L.logDebug(f'Checking subscriptions ({reason.name}) ri: {ri}')

		# ATTN: The "subscription" returned here are NOT the <sub> resources,
		# but an internal representation from the 'subscription' DB !!!
		# Access to attributes is different bc the structure is flattened
		if not (subs := CSE.storage.getSubscriptionsForParent(ri)):
			return
		for sub in subs:
			# Prevent own notifications for subscriptions 
			if childResource and \
				sub['ri'] == childResource.ri and \
				reason in [ NotificationEventType.createDirectChild, NotificationEventType.deleteDirectChild ]:
					continue
			if reason not in sub['net']:	# check whether reason is actually included in the subscription
				continue
			if reason in [ NotificationEventType.createDirectChild, NotificationEventType.deleteDirectChild ]:	# reasons for child resources
				chty = sub['chty']
				if chty and not childResource.ty in chty:	# skip if chty is set and child.type is not in the list
					continue
				self._handleSubscriptionNotification(sub, reason, resource=childResource, modifiedAttributes=modifiedAttributes)
			
			# Check Update and enc/atr vs the modified attributes 
			elif reason == NotificationEventType.resourceUpdate and (atr := sub['atr']) and modifiedAttributes:
				found = False
				for k in atr:
					if k in modifiedAttributes:
						found = True
				if found:
					self._handleSubscriptionNotification(sub, reason, resource=resource, modifiedAttributes=modifiedAttributes)
				else:
					L.isDebug and L.logDebug('Skipping notification: No matching attributes found')
			
			# Check for missing data points (only for <TS>)
			elif reason == NotificationEventType.reportOnGeneratedMissingDataPoints and missingData:
				md = missingData[sub['ri']]
				if md.missingDataCurrentNr >= md.missingDataNumber:	# Always send missing data if the count is greater then the minimum number
					self._handleSubscriptionNotification(sub, NotificationEventType.reportOnGeneratedMissingDataPoints, missingData=md)
					md.missingDataList = []	# delete only the sent missing data points

			else: # all other reasons that target the resource
				self._handleSubscriptionNotification(sub, reason, resource, modifiedAttributes=modifiedAttributes)


	###########################################################################
	#
	#	Notifications in general
	#

	def sendNotificationWithDict(self, data:JSON, nus:list[str]|str, originator:str = None) -> None:
		"""	Send a notification to a single URI or a list of URIs. A URI may be a resource ID, then
			the *poa* of that resource is taken. Also, the serialization is determined when 
			actually sending the notification.
		"""
		for nu in nus:
			self._sendRequest(nu, data, originator=originator)


	#########################################################################


	def _verifyNusInSubscription(self, subscription:Resource, previousNus:list[str] = None, originator:str = None) -> Result:
		"""	Check all the notification URI's in a subscription. A verification request is sent to new URI's. Notifications to the originator are not sent.

			If `previousNus` is given then only new nus are notified.
		"""
		if nus := subscription.nu:
			# notify new nus (verification request). New ones are the ones that are not in the previousNU list
			for nu in nus:
				if not previousNus or (nu not in previousNus):	# send only to new entries in nu
					# Skip notifications to originator
					if nu == originator:
						continue
					# Send verification notification to target (either direct URL, or an entity)
					if not self._sendVerificationRequest(nu, subscription.ri, originator=originator):
						# Return when even a single verification request fails
						return Result(status=False, rsc=RC.subscriptionVerificationInitiationFailed, dbg=f'Verification request failed for: {nu}')

		return Result(status=True)


	#########################################################################


	def _sendVerificationRequest(self, uri:Union[str, list[str]], ri:str, originator:str=None) -> bool:
		""""	Define the callback function for verification notifications and send
				the notification.
		"""

		def sender(uri:str) -> bool:
			L.isDebug and L.logDebug(f'Sending verification request to: {uri}')
			verificationRequest = {
				'm2m:sgn' : {
					'vrq' : True,
					'sur' : Utils.fullRI(ri)
				}
			}
			originator and Utils.setXPath(verificationRequest, 'm2m:sgn/cr', originator)
	
			if not self._sendRequest(uri, verificationRequest, noAccessIsError=True):
				L.isDebug and L.logDebug(f'Verification request failed for: {uri}')
				return False
			return True


		return self._sendNotification(uri, sender)


	def _sendDeletionNotification(self, uri:Union[str, list[str]], ri:str) -> bool:
		"""	Define the callback function for deletion notifications and send
			the notification
		"""

		def sender(uri:str) -> bool:
			L.isDebug and L.logDebug(f'Sending deletion notification to: {uri}')
			deletionNotification = {
				'm2m:sgn' : {
					'sud' : True,
					'sur' : Utils.fullRI(ri)
				}
			}

			if not self._sendRequest(uri, deletionNotification):
				L.isDebug and L.logDebug(f'Deletion request failed for: {uri}')
				return False
			return True


		return self._sendNotification(uri, sender) if uri else True	# Ignore if the uri is None


	def _handleSubscriptionNotification(self, sub:JSON, reason:NotificationEventType, resource:Resource=None, modifiedAttributes:JSON=None, missingData:MissingData=None) ->  bool:
		"""	Send a subscription notification.
		"""
		L.isDebug and L.logDebug(f'Handling notification for reason: {reason}')

		def sender(uri:str) -> bool:
			"""	Sender callback function for a single normal subscription notifications
			"""
			L.isDebug and L.logDebug(f'Sending notification to: {uri}, reason: {reason}	')
			notificationRequest = {
				'm2m:sgn' : {
					'nev' : {
						'rep' : {},
						'net' : NotificationEventType.resourceUpdate
					},
					'sur' : Utils.fullRI(sub['ri'])
				}
			}
			data = None
			nct = sub['nct']
			# switch
			nct == NotificationContentType.all						and (data := resource.asDict())
			nct == NotificationContentType.ri 						and (data := { 'm2m:uri' : resource.ri })
			nct == NotificationContentType.modifiedAttributes		and (data := { resource.tpe : modifiedAttributes })
			nct == NotificationContentType.timeSeriesNotification	and (data := { 'm2m:tsn' : missingData.asDict() })
			# TODO nct == NotificationContentType.triggerPayload

			# Add some values to the notification
			reason is not None and Utils.setXPath(notificationRequest, 'm2m:sgn/nev/net', reason)
			data is not None and Utils.setXPath(notificationRequest, 'm2m:sgn/nev/rep', data)

			# Check for batch notifications
			if sub['bn']:
				return self._storeBatchNotification(uri, sub, notificationRequest)
			else:
				if not self._sendRequest(uri, notificationRequest):
					L.isDebug and L.logDebug(f'Notification failed for: {uri}')
					return False
				return True

		result = self._sendNotification(sub['nus'], sender)	# ! This is not a <sub> resource, but the internal data structure, therefore 'nus

		# Handle subscription expiration in case of a successful notification
		if result and (exc := sub['exc']):
			L.isDebug and L.logDebug(f'Decrement expirationCounter: {exc} -> {exc-1}')

			exc -= 1
			subResource = CSE.storage.retrieveResource(ri=sub['ri']).resource
			if exc < 1:
				L.isDebug and L.logDebug(f'expirationCounter expired. Removing subscription: {subResource.ri}')
				CSE.dispatcher.deleteResource(subResource)	# This also deletes the internal sub
			else:
				subResource.setAttribute('exc', exc)		# Update the exc attribute
				subResource.dbUpdate()						# Update the real subscription
				CSE.storage.updateSubscription(subResource)	# Also update the internal sub
		return result								


	def _sendNotification(self, uris:Union[str, list[str]], senderFunction:SenderFunction) -> bool:
		"""	Send a notification to a single or to multiple targets if necessary. 
		
			Call the infividual callback functions to do the resource preparation and the the actual sending.

			Returns True, even when nothing was sent.
		"""
		#	Event when notification is happening, not sent
		CSE.event.notification() # type: ignore

		if isinstance(uris, str):
			return senderFunction(uris)
		else:
			for uri in uris:
				if not senderFunction(uri):
					return False
		return True


	def _sendRequest(self, uri:str, notificationRequest:JSON, parameters:Parameters=None, originator:str=None, targetOriginator:str=None, noAccessIsError:bool=False, ct:ContentSerializationType=None) -> bool:
		"""	Send a Notification request to a single target.
		"""
		result = CSE.request.sendNotifyRequest(	uri, 
												originator if originator else CSE.cseCsi,
												data=notificationRequest,
												parameters=parameters,
												ct=ct,
												targetOriginator=targetOriginator,
												noAccessIsError=noAccessIsError)
		return result.status and result.rsc == RC.OK


	##########################################################################
	#
	#	Batch Notifications
	#

	def _flushBatchNotifications(self, subscription:Resource) -> None:
		"""	Send and remove any outstanding batch notifications for a subscription.
		"""
		ri = subscription.ri
		# Get the subscription information (not the <sub> resource itself!).
		# Then get all the URIs/notification targets from that subscription. They might already
		# be filtered.
		if sub := CSE.storage.getSubscription(ri):
			ln = sub['ln'] if 'ln' in sub else False
			for nu in sub['nus']:
				self._stopNotificationBatchWorker(ri, nu)						# Stop a potential worker for that particular batch
				self._sendSubscriptionAggregatedBatchNotification(ri, nu, ln)	# Send all remaining notifications


	def _storeBatchNotification(self, nu:str, sub:JSON, notificationRequest:JSON) -> bool:
		"""	Store a subscription's notification for later sending. For a single nu.
		"""
		# Rename key name
		if 'm2m:sgn' in notificationRequest:
			notificationRequest['sgn'] = notificationRequest.pop('m2m:sgn')

		# Alway add the notification first before doing the other handling
		ri = sub['ri']
		CSE.storage.addBatchNotification(ri, nu, notificationRequest)

		#  Check for actions
		if (num := Utils.findXPath(sub, 'bn/num')) and CSE.storage.countBatchNotifications(ri, nu) >= num:
			ln = sub['ln'] if 'ln' in sub else False
			self._stopNotificationBatchWorker(ri, nu)	# Stop the worker, not needed
			self._sendSubscriptionAggregatedBatchNotification(ri, nu, ln)

		# Check / start Timer worker to guard the batch notification duration
		else:
			try:
				dur = isodate.parse_duration(Utils.findXPath(sub, 'bn/dur')).total_seconds()
			except Exception:
				return False
			self._startNewBatchNotificationWorker(ri, nu, dur)
		return True


	def _sendSubscriptionAggregatedBatchNotification(self, ri:str, nu:str, ln:bool=False) -> bool:
		"""	Send and remove(!) the available BatchNotifications for an ri & nu.
		"""
		with self.lockBatchNotification:
			L.isDebug and L.logDebug(f'Sending aggregated subscription notifications for ri: {ri}')

			# Collect the stored notifications for the batch and aggregate them
			notifications = []
			for notification in sorted(CSE.storage.getBatchNotifications(ri, nu), key=lambda x: x['tstamp']):	# type: ignore[no-any-return] # sort by timestamp added
				if n := Utils.findXPath(notification['request'], 'sgn'):
					notifications.append(n)
			if len(notifications) == 0:	# This can happen when the subscription is deleted and there are no outstanding notifications
				return False

			additionalParameters = None
			if ln:
				notifications = notifications[-1:]
				additionalParameters = { C.hfcEC : C.hfvECLatest }

			# Aggregate and send
			notificationRequest = {
				'm2m:agn' : { 'm2m:sgn' : notifications }
			}

			#	TODO check whether nu is an RI. Get that resource as target reosurce and pass it on to the send request
			#
			#	TODO This could actually be the part to handle batch notifications correctly. always store the target's ri
			#		 if it is a resource. only determine which poa and the ct later (ie here).
			#

			if not self._sendRequest(nu, notificationRequest, parameters=additionalParameters):
				L.isWarn and L.logWarn('Error sending aggregated batch notifications')
				return False

			# Delete old notifications
			if not CSE.storage.removeBatchNotifications(ri, nu):
				L.isWarn and L.logWarn('Error removing aggregated batch notifications')
				return False

			return True

# TODO expiration counter

	# def _checkExpirationCounter(self, sub:dict) -> bool:
	# 	if 'exc' in sub and (exc := sub['exc'] is not None:
	# 		if (subscription := CSE.dispatcher.retrieveResource(sub['ri']).resource) is None:
	# 			return False
	# 	return Result(status=True) if CSE.storage.updateSubscription(subscription) else Result(status=False, rsc=RC.internalServerError, dbg='cannot update subscription in database')


	def _startNewBatchNotificationWorker(self, ri:str, nu:str, dur:float) -> bool:
		if dur is None or dur < 1:	
			L.logErr('BatchNotification duration is < 1')
			return False
		L.isDebug and L.logDebug(f'Starting new batchNotificationsWorker. Duration : {dur:f} seconds')

		# Check and start a notification worker to send notifications after some time
		if len(BackgroundWorkerPool.findWorkers(self._workerID(ri, nu))) > 0:	# worker started, return
			return True
		BackgroundWorkerPool.newActor(self._sendSubscriptionAggregatedBatchNotification, delay=dur, name=self._workerID(ri, nu)).start(ri=ri, nu=nu)
		return True


	def _stopNotificationBatchWorker(self, ri:str, nu:str) -> None:
		BackgroundWorkerPool.stopWorkers(self._workerID(ri, nu))


	def _workerID(self, ri:str, nu:str) -> str:
		return f'{ri};{nu}'

