##############################################################################
#
# Copyright (c) 2001 Zope Corporation and Contributors. All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
""" Basic workflow tool.

$Id$
"""

import sys
from warnings import warn

from AccessControl.SecurityInfo import ClassSecurityInfo
from AccessControl.requestmethod import postonly
from Acquisition import aq_base, aq_inner, aq_parent
from App.class_init import default__class_init__ as InitializeClass
from App.special_dtml import DTMLFile
from OFS.Folder import Folder
from OFS.ObjectManager import IFAwareObjectManager
from Persistence import PersistentMapping
from zope.event import notify
from zope.interface import implements

from Products.CMFCore.ActionProviderBase import ActionProviderBase
from Products.CMFCore.interfaces import IConfigurableWorkflowTool
from Products.CMFCore.interfaces import IWorkflowDefinition
from Products.CMFCore.interfaces import IWorkflowTool
from Products.CMFCore.permissions import ManagePortal
from Products.CMFCore.utils import _dtmldir
from Products.CMFCore.utils import getToolByName
from Products.CMFCore.utils import Message as _
from Products.CMFCore.utils import UniqueObject
from Products.CMFCore.WorkflowCore import ActionRaisedExceptionEvent
from Products.CMFCore.WorkflowCore import ActionSucceededEvent
from Products.CMFCore.WorkflowCore import ActionWillBeInvokedEvent
from Products.CMFCore.WorkflowCore import ObjectDeleted
from Products.CMFCore.WorkflowCore import ObjectMoved
from Products.CMFCore.WorkflowCore import WorkflowException

_marker = []  # Create a new marker object.


class WorkflowTool(UniqueObject, IFAwareObjectManager, Folder,
                   ActionProviderBase):

    """ Mediator tool, mapping workflow objects
    """

    implements(IConfigurableWorkflowTool, IWorkflowTool)

    id = 'portal_workflow'
    meta_type = 'CMF Workflow Tool'
    _product_interfaces = (IWorkflowDefinition,)

    _chains_by_type = None  # PersistentMapping
    _default_chain = ('default_workflow',)
    _default_cataloging = 1

    security = ClassSecurityInfo()

    manage_options = ( { 'label' : 'Workflows'
                       , 'action' : 'manage_selectWorkflows'
                       }
                     , { 'label' : 'Overview', 'action' : 'manage_overview' }
                     ) + Folder.manage_options

    #
    #   ZMI methods
    #
    security.declareProtected( ManagePortal, 'manage_overview' )
    manage_overview = DTMLFile( 'explainWorkflowTool', _dtmldir )

    _manage_selectWorkflows = DTMLFile('selectWorkflows', _dtmldir)

    security.declareProtected( ManagePortal, 'manage_selectWorkflows')
    def manage_selectWorkflows(self, REQUEST, manage_tabs_message=None):

        """ Show a management screen for changing type to workflow connections.
        """
        cbt = self._chains_by_type
        ti = self._listTypeInfo()
        types_info = []
        for t in ti:
            id = t.getId()
            title = t.Title()
            if title == id:
                title = None
            if cbt is not None and cbt.has_key(id):
                chain = ', '.join(cbt[id])
            else:
                chain = '(Default)'
            types_info.append({'id': id,
                               'title': title,
                               'chain': chain})
        return self._manage_selectWorkflows(
            REQUEST,
            default_chain=', '.join(self._default_chain),
            types_info=types_info,
            management_view='Workflows',
            manage_tabs_message=manage_tabs_message)

    security.declareProtected( ManagePortal, 'manage_changeWorkflows')
    @postonly
    def manage_changeWorkflows(self, default_chain, props=None, REQUEST=None):
        """ Changes which workflows apply to objects of which type.
        """
        if props is None:
            props = REQUEST
        cbt = self._chains_by_type
        if cbt is None:
            self._chains_by_type = cbt = PersistentMapping()
        ti = self._listTypeInfo()
        # Set up the chains by type.
        if not (props is None):
            for t in ti:
                id = t.getId()
                field_name = 'chain_%s' % id
                chain = props.get(field_name, '(Default)').strip()
                if chain == '(Default)':
                    # Remove from cbt.
                    if cbt.has_key(id):
                        del cbt[id]
                else:
                    chain = chain.replace(',', ' ')
                    ids = []
                    for wf_id in chain.split(' '):
                        if wf_id:
                            if not self.getWorkflowById(wf_id):
                                raise ValueError, (
                                    '"%s" is not a workflow ID.' % wf_id)
                            ids.append(wf_id)
                    cbt[id] = tuple(ids)
        # Set up the default chain.
        default_chain = default_chain.replace(',', ' ')
        ids = []
        for wf_id in default_chain.split(' '):
            if wf_id:
                if not self.getWorkflowById(wf_id):
                    raise ValueError, (
                        '"%s" is not a workflow ID.' % wf_id)
                ids.append(wf_id)
        self._default_chain = tuple(ids)
        if REQUEST is not None:
            return self.manage_selectWorkflows(REQUEST,
                            manage_tabs_message='Changed.')

    #
    #   'IActionProvider' interface methods
    #
    security.declarePrivate('listActions')
    def listActions(self, info=None, object=None):

        """ Returns a list of actions to be displayed to the user.

        o Invoked by the portal_actions tool.

        o Allows workflows to include actions to be displayed in the
          actions box.

        o Object actions are supplied by workflows that apply to the object.

        o Global actions are supplied by all workflows.
        """
        if object is not None or info is None:
            info = self._getOAI(object)
        chain = self.getChainFor(info.object)
        did = {}
        actions = []

        for wf_id in chain:
            did[wf_id] = 1
            wf = self.getWorkflowById(wf_id)
            if wf is not None:
                a = wf.listObjectActions(info)
                if a is not None:
                    actions.extend(a)
                a = wf.listGlobalActions(info)
                if a is not None:
                    actions.extend(a)

        wf_ids = self.getWorkflowIds()
        for wf_id in wf_ids:
            if not did.has_key(wf_id):
                wf = self.getWorkflowById(wf_id)
                if wf is not None:
                    a = wf.listGlobalActions(info)
                    if a is not None:
                        actions.extend(a)
        return actions

    #
    #   'IWorkflowTool' interface methods
    #
    security.declarePrivate('getCatalogVariablesFor')
    def getCatalogVariablesFor(self, ob):
        """ Get a mapping of "workflow-relevant" attributes.
        """
        wfs = self.getWorkflowsFor(ob)
        if wfs is None:
            return None
        # Iterate through the workflows backwards so that
        # earlier workflows can override later workflows.
        wfs.reverse()
        vars = {}
        for wf in wfs:
            v = wf.getCatalogVariablesFor(ob)
            if v is not None:
                vars.update(v)
        return vars

    security.declarePublic('doActionFor')
    def doActionFor(self, ob, action, wf_id=None, *args, **kw):
        """ Perform the given workflow action on 'ob'.
        """
        wfs = self.getWorkflowsFor(ob)
        if wfs is None:
            wfs = ()
        if wf_id is None:
            if not wfs:
                raise WorkflowException(_(u'No workflows found.'))
            found = 0
            for wf in wfs:
                if wf.isActionSupported(ob, action, **kw):
                    found = 1
                    break
            if not found:
                msg = _(u"No workflow provides the '${action_id}' action.",
                        mapping={'action_id': action})
                raise WorkflowException(msg)
        else:
            wf = self.getWorkflowById(wf_id)
            if wf is None:
                raise WorkflowException(
                    _(u'Requested workflow definition not found.'))
        return self._invokeWithNotification(
            wfs, ob, action, wf.doActionFor, (ob, action) + args, kw)

    security.declarePublic('getInfoFor')
    def getInfoFor(self, ob, name, default=_marker, wf_id=None, *args, **kw):
        """ Get the given bit of workflow information for the object.
        """
        if wf_id is None:
            wfs = self.getWorkflowsFor(ob)
            if wfs is None:
                if default is _marker:
                    raise WorkflowException(_(u'No workflows found.'))
                else:
                    return default
            found = 0
            for wf in wfs:
                if wf.isInfoSupported(ob, name):
                    found = 1
                    break
            if not found:
                if default is _marker:
                    msg = _(u"No workflow provides '${name}' information.",
                            mapping={'name': name})
                    raise WorkflowException(msg)
                else:
                    return default
        else:
            wf = self.getWorkflowById(wf_id)
            if wf is None:
                if default is _marker:
                    raise WorkflowException(
                        _(u'Requested workflow definition not found.'))
                else:
                    return default
        res = wf.getInfoFor(ob, name, default, *args, **kw)
        if res is _marker:
            msg = _(u'Could not get info: ${name}', mapping={'name': name})
            raise WorkflowException(msg)
        return res

    security.declarePrivate('notifyCreated')
    def notifyCreated(self, ob):
        """ Notify all applicable workflows that an object has been created.
        """
        wfs = self.getWorkflowsFor(ob)
        for wf in wfs:
            wf.notifyCreated(ob)
        self._reindexWorkflowVariables(ob)

    security.declarePrivate('notifyBefore')
    def notifyBefore(self, ob, action):
        """ Notify all applicable workflows of an action before it happens.
        """
        wfs = self.getWorkflowsFor(ob)
        for wf in wfs:
            wf.notifyBefore(ob, action)
            notify(ActionWillBeInvokedEvent(ob, wf, action))

    security.declarePrivate('notifySuccess')
    def notifySuccess(self, ob, action, result=None):
        """ Notify all applicable workflows that an action has taken place.
        """
        wfs = self.getWorkflowsFor(ob)
        for wf in wfs:
            wf.notifySuccess(ob, action, result)
            notify(ActionSucceededEvent(ob, wf, action, result))

    security.declarePrivate('notifyException')
    def notifyException(self, ob, action, exc):
        """ Notify all applicable workflows that an action failed.
        """
        wfs = self.getWorkflowsFor(ob)
        for wf in wfs:
            wf.notifyException(ob, action, exc)
            notify(ActionRaisedExceptionEvent(ob, wf, action, exc))

    security.declarePrivate('getHistoryOf')
    def getHistoryOf(self, wf_id, ob):
        """ Get the history of an object for a given workflow.
        """
        if hasattr(aq_base(ob), 'workflow_history'):
            wfh = ob.workflow_history
            return wfh.get(wf_id, None)
        return ()

    security.declarePrivate('getStatusOf')
    def getStatusOf(self, wf_id, ob):
        """ Get the last element of a workflow history for a given workflow.
        """
        wfh = self.getHistoryOf(wf_id, ob)
        if wfh:
            return wfh[-1]
        return None

    security.declarePrivate('setStatusOf')
    def setStatusOf(self, wf_id, ob, status):
        """ Append a record to the workflow history of a given workflow.
        """
        wfh = None
        has_history = 0
        if hasattr(aq_base(ob), 'workflow_history'):
            history = ob.workflow_history
            if history is not None:
                has_history = 1
                wfh = history.get(wf_id, None)
                if wfh is not None:
                    wfh = list(wfh)
        if not wfh:
            wfh = []
        wfh.append(status)
        if not has_history:
            ob.workflow_history = PersistentMapping()
        ob.workflow_history[wf_id] = tuple(wfh)

    #
    #   'IConfigurableWorkflowTool' interface methods
    #
    security.declareProtected(ManagePortal, 'setDefaultChain')
    @postonly
    def setDefaultChain(self, default_chain, REQUEST=None):
        """ Set the default chain for this tool.
        """
        default_chain = default_chain.replace(',', ' ')
        ids = []
        for wf_id in default_chain.split(' '):
            if wf_id:
                if not self.getWorkflowById(wf_id):
                    raise ValueError('"%s" is not a workflow ID.' % wf_id)
                ids.append(wf_id)

        self._default_chain = tuple(ids)

    security.declareProtected(ManagePortal, 'setChainForPortalTypes')
    @postonly
    def setChainForPortalTypes(self, pt_names, chain, verify=True,
                               REQUEST=None):
        """ Set a chain for specific portal types.
        """
        cbt = self._chains_by_type
        if cbt is None:
            self._chains_by_type = cbt = PersistentMapping()

        if isinstance(chain, basestring):
            if chain == '(Default)':
                chain = None
            else:
                chain = [ wf.strip() for wf in chain.split(',') if wf.strip() ]

        if chain is None:
            for type_id in pt_names:
                if cbt.has_key(type_id):
                    del cbt[type_id]
            return

        ti_ids = [ t.getId() for t in self._listTypeInfo() ]

        for type_id in pt_names:
            if verify and not (type_id in ti_ids):
                continue
            cbt[type_id] = tuple(chain)

    security.declarePrivate('getDefaultChain')
    def getDefaultChain(self):
        """ Get the default chain for this tool.
        """
        return self._default_chain

    security.declarePrivate('listChainOverrides')
    def listChainOverrides(self):
        """ List portal type specific chain overrides.
        """
        cbt = self._chains_by_type
        return cbt and sorted(cbt.items()) or ()

    security.declarePrivate('getChainFor')
    def getChainFor(self, ob):
        """ Get the chain that applies to the given object.
        """
        cbt = self._chains_by_type
        if isinstance(ob, basestring):
            pt = ob
        elif hasattr(aq_base(ob), 'getPortalTypeName'):
            pt = ob.getPortalTypeName()
        else:
            pt = None

        if pt is None:
            return ()

        chain = None
        if cbt is not None:
            chain = cbt.get(pt, None)
            # Note that if chain is not in cbt or has a value of
            # None, we use a default chain.
        if chain is None:
            return self.getDefaultChain()
        return chain

    #
    #   Other methods
    #
    security.declareProtected(ManagePortal, 'updateRoleMappings')
    @postonly
    def updateRoleMappings(self, REQUEST=None):
        """ Allow workflows to update the role-permission mappings.
        """
        wfs = {}
        for id in self.objectIds():
            wf = self.getWorkflowById(id)
            if hasattr(aq_base(wf), 'updateRoleMappingsFor'):
                wfs[id] = wf
        portal = aq_parent(aq_inner(self))
        count = self._recursiveUpdateRoleMappings(portal, wfs)
        if REQUEST is not None:
            return self.manage_selectWorkflows(REQUEST, manage_tabs_message=
                                               '%d object(s) updated.' % count)
        else:
            return count

    security.declarePrivate('getWorkflowById')
    def getWorkflowById(self, wf_id):
        """ Retrieve a given workflow.
        """
        wf = getattr(self, wf_id, None)
        if IWorkflowDefinition.providedBy(wf):
            return wf
        if getattr(wf, '_isAWorkflow', False):
            # BBB
            warn("The '_isAWorkflow' marker attribute for workflow "
                 "definitions is deprecated and will be removed in "
                 "CMF 2.3;  please mark the definition with "
                 "'IWorkflowDefinition' instead.",
                 DeprecationWarning, stacklevel=2)
            return wf
        else:
            return None

    security.declarePrivate('getDefaultChainFor')
    def getDefaultChainFor(self, ob):
        """ Get the default chain, if applicable, for ob.
        """
        # XXX: this method violates the rules for tools/utilities:
        # it depends on a non-utility tool
        types_tool = getToolByName( self, 'portal_types', None )
        if ( types_tool is not None
            and types_tool.getTypeInfo( ob ) is not None ):
            return self._default_chain

        return ()

    security.declarePrivate('getWorkflowIds')
    def getWorkflowIds(self):

        """ Return the list of workflow ids.
        """
        wf_ids = []

        for obj_name, obj in self.objectItems():
            if IWorkflowDefinition.providedBy(obj):
                wf_ids.append(obj_name)
            elif getattr(obj, '_isAWorkflow', 0):
                # BBB
                warn("The '_isAWorkflow' marker attribute for workflow "
                     "definitions is deprecated and will be removed in "
                     "CMF 2.3;  please mark the definition with "
                     "'IWorkflowDefinition' instead.",
                     DeprecationWarning, stacklevel=2)
                wf_ids.append(obj_name)

        return tuple(wf_ids)

    security.declareProtected(ManagePortal, 'getWorkflowsFor')
    def getWorkflowsFor(self, ob):

        """ Find the workflows for the type of the given object.
        """
        res = []
        for wf_id in self.getChainFor(ob):
            wf = self.getWorkflowById(wf_id)
            if wf is not None:
                res.append(wf)
        return res

    #
    #   Helper methods
    #
    security.declarePrivate( '_listTypeInfo' )
    def _listTypeInfo(self):

        """ List the portal types which are available.
        """
        # XXX: this method violates the rules for tools/utilities:
        # it depends on a non-utility tool
        pt = getToolByName(self, 'portal_types', None)
        if pt is None:
            return ()
        else:
            return pt.listTypeInfo()

    security.declarePrivate( '_invokeWithNotification' )
    def _invokeWithNotification(self, wfs, ob, action, func, args, kw):

        """ Private utility method:  call 'func', and deal with exceptions
            indicating that the object has been deleted or moved.
        """
        reindex = 1
        for w in wfs:
            w.notifyBefore(ob, action)
            notify(ActionWillBeInvokedEvent(ob, w, action))
        try:
            res = func(*args, **kw)
        except ObjectDeleted, ex:
            res = ex.getResult()
            reindex = 0
        except ObjectMoved, ex:
            res = ex.getResult()
            ob = ex.getNewObject()
        except:
            exc = sys.exc_info()
            try:
                for w in wfs:
                    w.notifyException(ob, action, exc)
                    notify(ActionRaisedExceptionEvent(ob, w, action, exc))
                raise exc[0], exc[1], exc[2]
            finally:
                exc = None
        for w in wfs:
            w.notifySuccess(ob, action, res)
            notify(ActionSucceededEvent(ob, w, action, res))
        if reindex:
            self._reindexWorkflowVariables(ob)
        return res

    security.declarePrivate( '_recursiveUpdateRoleMappings' )
    def _recursiveUpdateRoleMappings(self, ob, wfs):

        """ Update roles-permission mappings recursively, and
            reindex special index.
        """
        # Returns a count of updated objects.
        count = 0
        wf_ids = self.getChainFor(ob)
        if wf_ids:
            changed = 0
            for wf_id in wf_ids:
                wf = wfs.get(wf_id, None)
                if wf is not None:
                    did = wf.updateRoleMappingsFor(ob)
                    if did:
                        changed = 1
            if changed:
                count = count + 1
                if hasattr(aq_base(ob), 'reindexObject'):
                    # Reindex security-related indexes
                    try:
                        ob.reindexObject(idxs=['allowedRolesAndUsers'])
                    except TypeError:
                        # Catch attempts to reindex portal_catalog.
                        pass
        if hasattr(aq_base(ob), 'objectItems'):
            obs = ob.objectItems()
            if obs:
                for k, v in obs:
                    changed = getattr(v, '_p_changed', 0)
                    count = count + self._recursiveUpdateRoleMappings(v, wfs)
                    if changed is None:
                        # Re-ghostify.
                        v._p_deactivate()
        return count

    security.declarePrivate( '_setDefaultCataloging' )
    def _setDefaultCataloging( self, value ):

        """ Toggle whether '_reindexWorkflowVariables' actually touches
            the catalog (sometimes not desirable, e.g. when the workflow
            objects do this themselves only at particular points).
        """
        self._default_cataloging = bool(value)

    security.declarePrivate('_reindexWorkflowVariables')
    def _reindexWorkflowVariables(self, ob):

        """ Reindex the variables that the workflow may have changed.

        Also reindexes the security.
        """
        if not self._default_cataloging:
            return

        if hasattr(aq_base(ob), 'reindexObject'):
            # XXX We only need the keys here, no need to compute values.
            mapping = self.getCatalogVariablesFor(ob) or {}
            vars = mapping.keys()
            ob.reindexObject(idxs=vars)

        # Reindex security of subobjects.
        if hasattr(aq_base(ob), 'reindexObjectSecurity'):
            ob.reindexObjectSecurity()

InitializeClass(WorkflowTool)
