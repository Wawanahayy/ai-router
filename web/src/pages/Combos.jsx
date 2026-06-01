import { useEffect, useState } from 'react'
import { getCombos, createCombo, updateCombo, deleteCombo, addComboModel, removeComboModel, updateComboModel, getProviders } from '../api'
import { Plus, Trash2, ChevronDown, ChevronUp, GripVertical, X } from 'lucide-react'
import ToggleSwitch from '../components/ToggleSwitch'

export default function Combos() {
	const [combos, setCombos] = useState([])
	const [providers, setProviders] = useState([])
	const [loading, setLoading] = useState(true)
	const [showAdd, setShowAdd] = useState(false)
	const [expanded, setExpanded] = useState(null)
	const [showAddModel, setShowAddModel] = useState(null)
	const [selectedProviderId, setSelectedProviderId] = useState('')
	const [upstreamModels, setUpstreamModels] = useState([])
	const [fetchingModels, setFetchingModels] = useState(false)
	const [pendingDelete, setPendingDelete] = useState(null)

	useEffect(() => { load() }, [])

	async function load() {
		try {
			const [c, p] = await Promise.all([getCombos(), getProviders()])
			setCombos(c)
			setProviders(p)
		} catch (e) { console.error(e) }
		finally { setLoading(false) }
	}

	const activeProvidersWithKeys = providers.filter(p => p.is_active && (p.key_stats?.alive || 0) > 0)

	function getModelsForProvider(providerId) {
		const p = providers.find(x => x.id === providerId)
		if (!p) return []
		return (p.aliases || []).filter(a => a.is_active !== 0)
	}

	const [providerModelsCache, setProviderModelsCache] = useState({})
	async function fetchProviderModels(providerId) {
		if (providerModelsCache[providerId]) return providerModelsCache[providerId]
		try {
			const res = await fetch(`/api/providers/${providerId}/models`)
			if (!res.ok) return []
			const data = await res.json()
			const models = (data.data || []).map(m => m.id).filter(Boolean)
			setProviderModelsCache(prev => ({ ...prev, [providerId]: models }))
			return models
		} catch (e) {
			console.error('Failed to fetch provider models:', e)
			return []
		}
	}

	async function handleAdd(e) {
		e.preventDefault()
		const fd = new FormData(e.target)
		const data = {
			name: fd.get('name'),
			description: fd.get('description') || '',
		}
		if (!data.name) return
		await createCombo(data)
		setShowAdd(false)
		e.target.reset()
		load()
	}

	async function handleDelete(id) {
		await deleteCombo(id)
		setPendingDelete(null)
		load()
	}

	async function handleToggle(id, active) {
		await updateCombo(id, { is_active: active ? 0 : 1 })
		load()
	}

	async function setComboMode(id, mode, currentMode) {
		if (mode === currentMode) return
		await updateCombo(id, { mode })
		load()
	}

	async function handleAddModel(comboId, e) {
		e.preventDefault()
		const fd = new FormData(e.target)
		const data = {
			provider_id: fd.get('provider_id'),
			model_id: fd.get('model_id'),
			alias: fd.get('alias') || '',
			sort_order: Number(fd.get('sort_order')) || 0,
		}
		if (!data.provider_id || !data.model_id) return
		await addComboModel(comboId, data)
		setShowAddModel(null)
		setSelectedProviderId('')
		e.target.reset()
		load()
	}

	async function handleRemoveModel(comboId, modelId) {
		await removeComboModel(comboId, modelId)
		setPendingDelete(null)
		load()
	}

	async function handleToggleModel(comboId, modelId, active) {
		await updateComboModel(comboId, modelId, { is_active: active ? 0 : 1 })
		load()
	}

	if (loading) return <div className="text-slate-500">Loading...</div>

	return (
		<div className="space-y-6">
			<div className="flex items-center justify-between">
				<div>
					<h2 className="text-2xl font-bold text-white">Combos</h2>
					<p className="text-xs text-slate-500 mt-1">Group multiple providers/models into a single name. Request with combo name = round-robin across all models in it. No restart needed - add/remove models anytime.</p>
				</div>
				<button onClick={() => setShowAdd(!showAdd)} className="btn-primary flex items-center gap-2 text-sm">
					<Plus size={16} /> Create Combo
				</button>
			</div>

			{showAdd && (
				<form onSubmit={handleAdd} className="card space-y-3">
					<div className="grid grid-cols-2 gap-4">
						<div>
							<label className="text-xs text-slate-500">Combo Name *</label>
							<input name="name" className="input" placeholder="e.g. smart, fast, coding" required />
						</div>
						<div>
							<label className="text-xs text-slate-500">Description</label>
							<input name="description" className="input" placeholder="Optional description" />
						</div>
					</div>
					<div className="flex gap-2">
						<button type="submit" className="btn-primary text-sm">Create</button>
						<button type="button" onClick={() => setShowAdd(false)} className="btn-ghost text-sm">Cancel</button>
					</div>
				</form>
			)}

			{combos.length === 0 ? (
				<div className="card text-slate-500 text-sm">No combos yet. Create one to group providers/models.</div>
			) : (
				<div className="space-y-3">
					{combos.map(combo => (
						<div key={combo.id} className="card">
							<div className="flex items-center justify-between">
								<div className="flex items-center gap-3">
									<button onClick={() => setExpanded(expanded === combo.id ? null : combo.id)} className="text-slate-500 hover:text-slate-300">
										{expanded === combo.id ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
									</button>
									<div>
										<div className="flex items-center gap-2">
											<span className="font-semibold text-white">{combo.name}</span>
											<span className={`text-xs px-2 py-0.5 rounded-full ${combo.is_active ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
												{combo.is_active ? 'Active' : 'Disabled'}
											</span>
											<span className="text-xs text-slate-500">{combo.model_count} model(s)</span>
										</div>
										{combo.description && <p className="text-xs text-slate-500 mt-0.5">{combo.description}</p>}
									</div>
								</div>
								<div className="flex items-center gap-2">
									<div className="mode-switch" aria-label="Combo routing mode">
										<button
											type="button"
											onClick={() => setComboMode(combo.id, 'round_robin', combo.mode)}
											className={combo.mode !== 'single' ? 'is-active' : ''}
										>
											Round-Robin
										</button>
										<button
											type="button"
											onClick={() => setComboMode(combo.id, 'single', combo.mode)}
											className={`single ${combo.mode === 'single' ? 'is-active' : ''}`}
										>
											Single
										</button>
									</div>
									<div className="flex items-center gap-2 text-xs text-slate-400">
										<ToggleSwitch
											checked={combo.is_active !== 0}
											onChange={() => handleToggle(combo.id, combo.is_active)}
											label={combo.is_active ? 'Disable combo' : 'Enable combo'}
										/>
										<span>{combo.is_active ? 'Enabled' : 'Disabled'}</span>
									</div>
									{pendingDelete === `combo:${combo.id}` ? (
										<div className="inline-confirm">
											<span>Delete combo?</span>
											<button onClick={() => handleDelete(combo.id)} className="btn-danger text-xs">Delete</button>
											<button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
										</div>
									) : (
										<button onClick={() => setPendingDelete(`combo:${combo.id}`)} className="btn-danger text-xs flex items-center gap-1" title="Delete combo">
											<Trash2 size={12} /> Delete Combo
										</button>
									)}
								</div>
							</div>

							{expanded === combo.id && (
								<div className="mt-4 space-y-3 border-t border-[#1e1e2e] pt-4">
									{combo.models && combo.models.length > 0 ? (
										<div className="space-y-2">
											<h4 className="text-xs font-semibold text-slate-400 uppercase">Models in this combo</h4>
											{combo.models.map(m => (
												<div key={m.id} className="flex items-center gap-3 bg-[#0d0d15] rounded-lg px-3 py-2">
													<GripVertical size={14} className="text-slate-600" />
													<div className="flex-1">
														<div className="flex items-center gap-2">
															<span className="text-sm text-white font-mono">{m.model_id}</span>
															{m.alias && <span className="text-xs text-slate-500">-&gt; {m.alias}</span>}
															<span className="text-xs text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded">{m.provider_name || m.provider_id}</span>
														</div>
													</div>
													<div className="flex items-center gap-2 text-xs text-slate-400">
														<ToggleSwitch
															checked={m.is_active !== 0}
															onChange={() => handleToggleModel(combo.id, m.id, m.is_active)}
															label={m.is_active ? 'Disable model' : 'Enable model'}
														/>
														<span>{m.is_active ? 'Active' : 'Off'}</span>
													</div>
													{pendingDelete === `combo-model:${m.id}` ? (
														<div className="inline-confirm">
															<span>Delete model?</span>
															<button onClick={() => handleRemoveModel(combo.id, m.id)} className="btn-danger text-xs">Delete</button>
															<button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
														</div>
													) : (
														<button onClick={() => setPendingDelete(`combo-model:${m.id}`)} className="btn-danger text-xs flex items-center gap-1">
															<X size={12} /> Delete Model
														</button>
													)}
												</div>
											))}
										</div>
									) : (
										<p className="text-xs text-slate-500">No models in this combo yet.</p>
									)}

									{showAddModel === combo.id ? (
										<form onSubmit={(e) => handleAddModel(combo.id, e)} className="bg-[#0d0d15] rounded-lg p-3 space-y-2">
											<h4 className="text-xs font-semibold text-slate-400">Add Model to Combo</h4>
											{activeProvidersWithKeys.length === 0 ? (
												<p className="text-xs text-amber-400">No active providers with alive keys. Add providers and keys first.</p>
											) : (
												<div className="grid grid-cols-2 gap-3">
													<div>
														<label className="text-xs text-slate-500">Provider *</label>
														<select
															name="provider_id"
															className="input"
															required
															value={selectedProviderId}
															onChange={async e => {
																setSelectedProviderId(e.target.value)
																setUpstreamModels([])
																const pid = e.target.value
																if (pid && getModelsForProvider(pid).length === 0) {
																	setFetchingModels(true)
																	const models = await fetchProviderModels(pid)
																	setUpstreamModels(models)
																	setFetchingModels(false)
																}
															}}
														>
															<option value="">Select provider...</option>
															{activeProvidersWithKeys.map(p => (
																<option key={p.id} value={p.id}>{p.name} ({p.key_stats?.alive || 0} keys)</option>
															))}
														</select>
													</div>
													<div>
														<label className="text-xs text-slate-500">Model *</label>
														{selectedProviderId && getModelsForProvider(selectedProviderId).length > 0 ? (
															<select name="model_id" className="input" required>
																<option value="">Select model...</option>
																{getModelsForProvider(selectedProviderId).map(a => (
																	<option key={a.alias} value={a.model_id}>{a.model_id} ({a.alias})</option>
																))}
															</select>
														) : selectedProviderId ? (
															<div className="space-y-2">
																{fetchingModels ? (
																	<input name="model_id" className="input" placeholder="Loading models..." disabled />
																) : upstreamModels.length > 0 ? (
																	<select name="model_id" className="input" required>
																		<option value="">Select model...</option>
																		{upstreamModels.map(mid => (
																			<option key={mid} value={mid}>{mid}</option>
																		))}
																	</select>
																) : (
																	<input name="model_id" className="input" placeholder="Type model ID" required />
																)}
																{upstreamModels.length === 0 && !fetchingModels && <span className="text-xs text-amber-500">No aliases or upstream models found - type model ID manually</span>}
															</div>
														) : (
															<input name="model_id" className="input" placeholder="Select a provider first" disabled />
														)}
													</div>
													<div>
														<label className="text-xs text-slate-500">Alias (optional)</label>
														<input name="alias" className="input" placeholder="Custom name for this model in combo" />
													</div>
													<div>
														<label className="text-xs text-slate-500">Sort Order</label>
														<input name="sort_order" type="number" className="input" defaultValue="0" />
													</div>
												</div>
											)}
											<div className="flex gap-2">
												<button type="submit" className="btn-primary text-xs" disabled={activeProvidersWithKeys.length === 0}>Add</button>
												<button type="button" onClick={() => { setShowAddModel(null); setSelectedProviderId(''); setUpstreamModels([]) }} className="btn-ghost text-xs">Cancel</button>
											</div>
										</form>
									) : (
										<button onClick={() => { setShowAddModel(combo.id); setSelectedProviderId(''); setUpstreamModels([]) }} className="btn-ghost text-xs flex items-center gap-1">
											<Plus size={14} /> Add Model
										</button>
									)}
								</div>
							)}
						</div>
					))}
				</div>
			)}
		</div>
	)
}
