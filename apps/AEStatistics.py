#
#	AEStatistics.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	An AE to store statistics about the CSE in a flexContainer.
#


from AEBase import *
from Logging import Logging
from Configuration import Configuration
import Statistics
import threading, time


class AEStatistics(AEBase):

	def __init__(self):
		super().__init__(	rn=Configuration.get('app.statistics.aeRN'), 
							api=Configuration.get('app.statistics.aeAPI'), 
							aei=Configuration.get('app.statistics.aeAEI'),
							acpri=Configuration.get('app.statistics.acpri'),
							originator=Configuration.get('app.statistics.originator'),
							nodeRN=Configuration.get('app.csenode.nodeRN'),					# From CSE-Node
							nodeID=Configuration.get('app.csenode.nodeID')					# From CSE-Node
						)

		self.fcsrn = self.srn + '/' + Configuration.get('app.statistics.fcntRN')
		self.fcntType = Configuration.get('app.statistics.fcntType')

		# Create structure beneath the AE resource
		self.fc = self.retrieveCreate(	srn=self.fcsrn,
										jsn={ self.fcntType : {
												'rn'  : Configuration.get('app.statistics.fcntRN'),
       											'cnd' : Configuration.get('app.statistics.fcntCND'),
												Statistics.deletedResources : 0,
												Statistics.createdresources : 0,
												Statistics.httpRetrieves : 0,
												Statistics.httpCreates : 0,
												Statistics.httpUpdates : 0,
												Statistics.httpDeletes : 0,
												Statistics.logErrors : 0,
												Statistics.logWarnings : 0,
												Statistics.cseStartUpTime : '',
												Statistics.cseUpTime : '',
												Statistics.resourceCount: 0
											}
										},
										ty=C.tFCNT)

		# Update the statistic resource from time to time
		self.startWorker(Configuration.get('app.statistics.intervall'), self.statisticsWorker)

		Logging.log('AEStatistics AE registered')


	def shutdown(self):
		super().shutdown()
		Logging.log('AEStatistics AE shut down')


	# Worker for the update thread
	def statisticsWorker(self):
		Logging.logDebug('Statistics worker thread started')
		while not self.doStopWorker:
			Logging.logDebug('Updating statistics')
			self.updateStats()
			self.sleep()


	def updateStats(self):
		stats = CSE.statistics.getStats()
		self.updateResource(srn=self.fcsrn, jsn={ self.fcntType : stats })
