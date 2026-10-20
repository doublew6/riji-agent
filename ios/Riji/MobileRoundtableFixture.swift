#if DEBUG
import Foundation
import RijiCore

/// Explicit synthetic UI namespace only. No model, network, or real diary is accessed.
actor MobileRoundtableFixture {
    private let file: URL
    private let seedLegacyHistory: Bool
    private var records: [String: [String: Any]] = [:]
    private var receipts: [String: [String: Any]] = [:]
    private var results: [String: [String: Any]] = [:]
    private var currentID: String?
    private let mentors = ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"]
    init(file: URL, seedLegacyHistory: Bool = false) {
        self.file = file
        self.seedLegacyHistory = seedLegacyHistory
        if let data = try? Data(contentsOf: file), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            records = object["records"] as? [String: [String: Any]] ?? [:]
            receipts = object["receipts"] as? [String: [String: Any]] ?? [:]
            results = object["results"] as? [String: [String: Any]] ?? [:]
            currentID = object["current"] as? String
        }
    }
    func response(_ request: URLRequest, memories: [[String: Any]] = []) throws -> (Data, HTTPURLResponse)? {
        guard let url = request.url else { return nil }
        let parts = url.pathComponents
        guard parts.count >= 4 else { return nil }
        if parts[3] == "operations", let receipt = receipts[url.lastPathComponent] { return try encode(receipt, url: url) }
        guard parts[3] == "roundtables" else { return nil }
        let body = request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        if let op = body["operation_id"] as? String, let receipt = receipts[op] { return try encode(receipt, url: url) }
        if parts.count == 4, request.httpMethod == "GET" {
            // Existing legacy history keeps compatibility paths testable without a new-host entry.
            if seedLegacyHistory && records.isEmpty {
                let identifier = "00000000-0000-4000-8000-000000000056"
                var legacy = base(identifier)
                append("已有的合成圆桌记录", actor: "user", kind: "user", value: &legacy)
                append("这是保留的旧圆桌，可以继续查看。", actor: "host", kind: "followup", value: &legacy)
                records[identifier] = legacy
                currentID = identifier
                try save()
            }
            let items = records.values.map { value -> [String: Any] in
                let messages = value["messages"] as? [[String: Any]] ?? []
                return ["id": value["conversation_id"]!, "title": value["title"] ?? messages.first?["text"] ?? "圆桌", "status": value["status"]!, "updated_at": value["updated_at"] ?? Date().timeIntervalSince1970, "revision": value["revision"]!, "chat_kind": value["chat_kind"] ?? "legacy", "personas": value["personas"] ?? []]
            }
            return try encode(["items": items, "current_id": currentID as Any? ?? NSNull(), "truncated": false], url: url)
        }
        if parts.count == 4 {
            let id = UUID().uuidString
            var value = base(id)
            if body["chat_kind"] as? String == "group" {
                let members = body["personas"] as? [String] ?? []
                let names = ["gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练", "future_self": "未来的我", "wang_yangming": "王阳明"]
                value["chat_kind"] = "group"; value["personas"] = members; value["audience"] = members
                value["title"] = members.map { names[$0] ?? "导师" }.joined(separator: "、")
                value["run_personas"] = []; value["run_kind"] = "group"
            } else {
                append(body["text"] as? String ?? "我想每周留出两小时学习。", actor: "user", kind: "user", value: &value, origin: body["statement_kind"] as? String ?? "user_statement")
                append("可以先把目标缩成一个两周实验。你最希望改善什么？普通发送只由我接话，需要比较不同意见时再邀请导师。", actor: "host", kind: "followup", value: &value)
            }
            records[id] = value; currentID = id
            return try receipt(body, kind: "roundtable_create", result: ["conversation_id": id], url: url)
        }
        let id = parts[4]
        guard var value = records[id] else { return try encode(["detail": "roundtable_not_found"], url: url, status: 404) }
        if parts.count == 5 {
            if value["status"] as? String == "running" {
                value["status"] = "completed"
                append("共同建议：先试两周，再依据实际执行反馈调整。分歧在于时间安排：固定时段更稳定，弹性时间更容易开始。前提是每周至少有一个不受打扰的时段。下一步：选一个最小行动，记录是否执行。", actor: "host", kind: "comparison", value: &value)
                records[id] = value; try save()
            }
            value["history_version"] = SourceHash.digest(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]))
            value["cache_expires_at"] = Date().timeIntervalSince1970 + 900
            return try encode(value, url: url)
        }
        switch parts[5] {
        case "messages":
            value["revision"] = (value["revision"] as? Int ?? 1) + 1
            append(body["text"] as? String ?? "", actor: "user", kind: "user", value: &value, origin: body["statement_kind"] as? String ?? "user_statement")
            if value["chat_kind"] as? String == "group" {
                value["run_id"] = UUID().uuidString
                let requested = body["reply_personas"] as? [String] ?? []
                let responders = requested.isEmpty ? value["personas"] as? [String] ?? [] : requested
                value["run_personas"] = responders
                for actor in responders { append("这是一段合成测试回复：先选一个可以验证的小行动，再根据实际反馈调整。", actor: actor, kind: "opinion", value: &value) }
            } else { append("收到这次补充。我会把它作为当前条件；建议与实际反馈分别保留。如果条件改变了，可以先总结或纠正旧背景。", actor: "host", kind: "followup", value: &value) }
            value["status"] = "completed"; records[id] = value
            return try receipt(body, kind: "roundtable_message", result: ["conversation_id": id], url: url)
        case "materials":
            let selected = body["memories"] as? [[String: String]] ?? []
            let chosen = selected.compactMap { choice in memories.first { $0["id"] as? String == choice["id"] && $0["version"] as? String == choice["expected_version"] } }
            return try encode(["preview_hash": String(repeating: "a", count: 64), "artifact_ids": body["artifact_ids"] ?? [], "memories": chosen, "audience": ["host"] + mentors, "personas": body["personas"] ?? [], "mode": body["mode"] ?? "reference", "rounds": body["rounds"] ?? 1, "revision": value["revision"]!], url: url)
        case "commands":
            let action = body["action"] as? String ?? ""
            value["revision"] = (value["revision"] as? Int ?? 1) + 1
            if action == "rename" { value["title"] = body["text"] as? String ?? "" }
            else if action == "start_run" {
                value["run_id"] = UUID().uuidString; value["run_kind"] = "roundtable"; value["status"] = "running"
                value["mode"] = body["mode"] ?? "reference"; value["rounds"] = body["rounds"] ?? 1
                value["run_personas"] = body["personas"] ?? []
                for actor in body["personas"] as? [String] ?? [] {
                    let advice = ["gentle_reviewer": "建议先降低起步难度，每周做一个小练习，避免把学习变成额外压力。", "blunt_coach": "如果不明确开始时间，很容易继续拖延。先在日历里固定一个时段，再检查真实执行。", "future_self": "长期看，留下每周可复用的成果比累计时长更有价值。", "wang_yangming": "把理解落实在一次具体行动中，再用反馈校正认识。"]
                    append(advice[actor] ?? "先试一次再看反馈。", actor: actor, kind: "opinion", value: &value)
                }
            } else if action == "stop" { value["status"] = "stopped" }
            else if action == "archive" { value["status"] = "archived" }
            else if action == "restore" { value["status"] = "stopped" }
            else if action == "correct" {
                value["correction_version"] = (value["correction_version"] as? Int ?? 0) + 1
                var messages = value["messages"] as? [[String: Any]] ?? []
                let selected = Set(body["supersedes"] as? [String] ?? [])
                for index in messages.indices where messages[index]["actor"] as? String != "user" || selected.contains(messages[index]["id"] as? String ?? "") { messages[index]["superseded"] = true }
                value["messages"] = messages
                append(body["text"] as? String ?? "条件已改变", actor: "user", kind: "user", value: &value)
                value["status"] = "waiting_user"
            } else {
                value["status"] = "completed"
                append(action == "summarize" ? "部分总结：已完成的建议供参考，未完成视角仍缺失。请结合新的条件再决定下一步。" : "我们按最新条件继续。先选一个可验证的小行动，再回来记录实际反馈。", actor: "host", kind: "synthesis", value: &value)
            }
            records[id] = value
            return try receipt(body, kind: "roundtable_command", result: ["conversation_id": id], url: url)
        case "result-preview":
            let chosen = Set(body["artifact_ids"] as? [String] ?? [])
            let messages = (value["messages"] as? [[String: Any]] ?? []).filter { chosen.contains($0["id"] as? String ?? "") }
            let resultID = UUID().uuidString
            let provenance: [String: Any] = ["result_id": resultID, "problem_id": id, "discussion_id": value["run_id"]!, "artifact_ids": Array(chosen), "input_revision": value["revision"]!, "summary_version": 1, "correction_version": value["correction_version"]!, "recorded_at": ISO8601DateFormatter().string(from: Date()), "mentors": value["run_personas"] as? [String] ?? ["host"], "topic": "两周学习实验", "saved_date": "", "edited_by_user": false]
            results[resultID] = ["result_id": resultID, "text": messages.compactMap { $0["text"] as? String }.joined(separator: "\n\n"), "provenance": provenance, "binding": String(repeating: "b", count: 64)]
            return try receipt(body, kind: "roundtable_result", result: ["result_id": resultID], url: url)
        case "result-validation":
            guard var result = results[body["result_id"] as? String ?? ""], let provenance = result["provenance"] as? [String: Any], provenance["correction_version"] as? Int == value["correction_version"] as? Int else {
                return try encode(["detail": "handoff_source_unavailable"], url: url, status: 409)
            }
            result["validated_at"] = Date().timeIntervalSince1970
            return try encode(result, url: url)
        default: return try encode(["detail": "not_found"], url: url, status: 404)
        }
    }
    private func base(_ id: String) -> [String: Any] {
        ["conversation_id": id, "status": "completed", "revision": 1, "state_revision": 1, "summary_version": 1, "correction_version": 0, "run_id": UUID().uuidString, "run_kind": "followup", "run_personas": ["host"], "mode": "reference", "rounds": 1, "suspended_run_id": "", "messages": [], "current_summary": NSNull(), "runs": [], "audience": ["host"] + mentors, "notice": NSNull(), "selected_artifact_ids": [], "next_cursor": NSNull()]
    }
    private func append(_ text: String, actor: String, kind: String, value: inout [String: Any], origin: String = "user_statement") {
        var messages = value["messages"] as? [[String: Any]] ?? []
        messages.append(["id": UUID().uuidString, "actor": actor, "kind": kind, "text": text, "created_at": Date().timeIntervalSince1970, "input_revision": value["revision"]!, "run_id": value["run_id"]!, "origin_kind": actor == "user" ? origin : "ai_discussion", "superseded": false, "unavailable": NSNull(), "uncertainties": [], "next_steps": [], "source_ids": []])
        value["messages"] = messages; value["updated_at"] = Date().timeIntervalSince1970
    }
    private func receipt(_ body: [String: Any], kind: String, result: [String: Any], url: URL) throws -> (Data, HTTPURLResponse) {
        let id = body["operation_id"] as? String ?? UUID().uuidString
        let receipt: [String: Any] = ["operation_id": id, "kind": kind, "state": "completed", "result": result, "error": NSNull()]
        receipts[id] = receipt; try save(); return try encode(receipt, url: url)
    }
    private func save() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: ["records": records, "receipts": receipts, "results": results, "current": currentID as Any? ?? NSNull()]).write(to: file, options: .atomic)
    }
    private func encode(_ value: [String: Any], url: URL, status: Int = 200) throws -> (Data, HTTPURLResponse) {
        (try JSONSerialization.data(withJSONObject: value), HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
}
#endif

#if DEBUG && targetEnvironment(simulator)
import UIKit
import ObjectiveC.runtime
// Temporary metadata-only diagnosis. Explicit synthetic fixtures arm only at the target stage.
@MainActor enum SyntheticTouchTrace {
    enum Mode: String { case roundtable = "roundtable-selection", northStar = "north-star-definition" }
    enum Action: String, Sendable {
        case armed, multiselectEnter, multiselectReturn, northStarEnter, northStarReturn
        case northStarTaskBegin, northStarTaskEnd, inboxBegin, inboxEnd, foregroundTickBegin, foregroundTickEnd
        case incompleteTouchSnapshot, stopped
    }
    private struct Ancestor: Sendable {
        let name: String
        let gestures: [String]
        let states: [Int]
    }
    private struct TouchMetadata: Sendable {
        let stage: String
        let eventTime: Double
        let touchTime: Double
        let handledTime: Double
        let phase: Int
        let x: Double
        let y: Double
        let window: String
        let target: String
        let ancestors: [Ancestor]
    }
    private struct ActionMetadata: Sendable {
        let action: Action
        let time: Double
        let busy: Bool?
        let preview: Bool?
        let selecting: Bool?
        let count: Int?
    }
    private enum Entry: Sendable { case touch(TouchMetadata), action(ActionMetadata) }
    struct PendingTouch {
        let touch: UITouch
        let eventTime: Double
        let ancestors: [UIView]
    }
    private static var installedMode: Mode?
    private static var didArm = false
    private static var armed = false
    private static var deadline = 0.0
    private static var activeTouches: Set<ObjectIdentifier> = []
    private static var entries: [Entry] = []
    private static var flushScheduled = false
    private static var forceFlush = false
    private static var watchdogScheduled = false
    private static var emittedEntries = 0
    private static let output = DispatchQueue(label: "riji.synthetic-touch-trace")

    static func installIfRequested() {
        let environment = ProcessInfo.processInfo.environment
        guard installedMode == nil,
              environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil,
              environment["RIJI_TEST_ORIGINAL_VAULT"] == nil,
              let mode = environment["RIJI_TEST_TOUCH_TRACE"].flatMap(Mode.init(rawValue:)) else { return }
        let permitted = mode == .roundtable ? environment["RIJI_TEST_MOBILE"] == "roundtable"
            : environment["RIJI_TEST_MOBILE"] == nil
        guard permitted,
              let original = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.sendEvent(_:))),
              let trace = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.riji_traceSyntheticTouch(_:))) else { return }
        method_exchangeImplementations(original, trace)
        installedMode = mode
    }

    static func arm(_ mode: Mode) {
        guard installedMode == mode, !didArm else { return }
        didArm = true; armed = true
        deadline = ProcessInfo.processInfo.systemUptime + 120
        mark(.armed)
    }

    static func mark(_ action: Action, busy: Bool? = nil, preview: Bool? = nil,
                     selecting: Bool? = nil, count: Int? = nil) {
        guard isRecording else { return }
        append(.action(ActionMetadata(action: action, time: ProcessInfo.processInfo.systemUptime,
                                     busy: busy, preview: preview, selecting: selecting, count: count)))
        scheduleFlush()
    }

    static func stop() {
        mark(.stopped, count: activeTouches.count)
        armed = false
        // The next main-queue turn runs after the current event's after-record is appended.
        scheduleFlush(force: true)
    }

    private static var isRecording: Bool {
        armed && ProcessInfo.processInfo.systemUptime <= deadline && emittedEntries < 1_024
    }

    static func capture(_ event: UIEvent, in window: UIWindow) -> [PendingTouch] {
        guard event.type == .touches, isRecording || !activeTouches.isEmpty else { return [] }
        var pending: [PendingTouch] = []
        for touch in (event.allTouches ?? []).prefix(4) where touch.window === window {
            guard [.began, .ended, .cancelled].contains(touch.phase) else { continue }
            let identity = ObjectIdentifier(touch)
            if touch.phase == .began {
                guard isRecording, activeTouches.count < 4 || activeTouches.contains(identity) else { continue }
                activeTouches.insert(identity)
            } else if !activeTouches.contains(identity) { continue }
            var ancestors: [UIView] = []
            var view = touch.view
            while let current = view, ancestors.count < 24 {
                ancestors.append(current); view = current.superview
            }
            let record = PendingTouch(touch: touch, eventTime: event.timestamp, ancestors: ancestors)
            append(.touch(metadata(record, in: window, stage: "before")))
            pending.append(record)
        }
        return pending
    }

    static func finish(_ pending: [PendingTouch], in window: UIWindow) {
        for record in pending {
            append(.touch(metadata(record, in: window, stage: "after")))
            if [.ended, .cancelled].contains(record.touch.phase) {
                activeTouches.remove(ObjectIdentifier(record.touch))
            }
        }
        scheduleFlush()
    }

    private static func metadata(_ record: PendingTouch, in window: UIWindow, stage: String) -> TouchMetadata {
        let point = record.touch.location(in: window)
        let ancestors = record.ancestors.map { view in
            let gestures = Array((view.gestureRecognizers ?? []).prefix(8))
            return Ancestor(name: className(view), gestures: gestures.map(className),
                            states: gestures.map { $0.state.rawValue })
        }
        return TouchMetadata(stage: stage, eventTime: record.eventTime, touchTime: record.touch.timestamp,
            handledTime: ProcessInfo.processInfo.systemUptime, phase: record.touch.phase.rawValue,
            x: Double(point.x), y: Double(point.y), window: className(window),
            target: record.touch.view.map(className) ?? "none", ancestors: ancestors)
    }

    private static func className(_ object: NSObject) -> String {
        String(NSStringFromClass(type(of: object)).prefix(160))
    }

    private static func append(_ entry: Entry) {
        if entries.count == 192 { entries.removeFirst() }
        entries.append(entry)
        scheduleWatchdog()
    }

    private static func scheduleWatchdog() {
        guard !watchdogScheduled, !activeTouches.isEmpty, emittedEntries < 1_024 else { return }
        watchdogScheduled = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            watchdogScheduled = false
            guard !activeTouches.isEmpty, !entries.isEmpty else { return }
            append(.action(ActionMetadata(action: .incompleteTouchSnapshot,
                time: ProcessInfo.processInfo.systemUptime, busy: nil, preview: nil,
                selecting: nil, count: activeTouches.count)))
            // Preserve missing-end evidence without changing touches or their tracked state.
            scheduleFlush(force: true)
        }
    }

    private static func scheduleFlush(force: Bool = false) {
        if force { forceFlush = true }
        guard !flushScheduled, activeTouches.isEmpty || forceFlush, !entries.isEmpty else { return }
        flushScheduled = true
        DispatchQueue.main.async {
            flushScheduled = false
            guard activeTouches.isEmpty || forceFlush else { return }
            forceFlush = false
            let batch = Array(entries.suffix(max(0, 1_024 - emittedEntries)))
            entries.removeAll(keepingCapacity: true)
            emittedEntries += batch.count
            output.async { for entry in batch { print(format(entry)) } }
        }
    }

    nonisolated private static func format(_ entry: Entry) -> String {
        switch entry {
        case .touch(let value):
            let chain = value.ancestors.map { ancestor in
                ancestor.name + "[" + zip(ancestor.gestures, ancestor.states).map { "\($0):\($1)" }.joined(separator: ",") + "]"
            }.joined(separator: ">")
            return "[DEBUG-touch-dispatch] \(value.stage) event=\(value.eventTime) touch=\(value.touchTime) handled=\(value.handledTime) phase=\(value.phase) point=(\(value.x),\(value.y)) window=\(value.window) target=\(value.target) ancestors=\(chain)"
        case .action(let value):
            return "[DEBUG-touch-action] \(value.action.rawValue) handled=\(value.time) busy=\(flag(value.busy)) preview=\(flag(value.preview)) selecting=\(flag(value.selecting)) count=\(value.count.map(String.init) ?? "none")"
        }
    }

    nonisolated private static func flag(_ value: Bool?) -> String {
        value.map { $0 ? "true" : "false" } ?? "none"
    }
}

private extension UIWindow {
    @objc func riji_traceSyntheticTouch(_ event: UIEvent) {
        let pending = SyntheticTouchTrace.capture(event, in: self)
        // The exchanged selector forwards to the original implementation exactly once.
        riji_traceSyntheticTouch(event)
        SyntheticTouchTrace.finish(pending, in: self)
    }
}
#endif
