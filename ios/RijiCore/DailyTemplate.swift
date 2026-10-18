import Foundation

public enum DailyTemplate {
    public static let label = "产品初始模板"
    public static let sections = ["🌅 Morning", "🌆 Evening", "🪞 Self Reflection", "📚 Daily Learning", "🧠 Notes"]
    public static let initial = """
    # {{title}}

    ## 🌅 Morning

    ## 🌆 Evening

    ## 🪞 Self Reflection

    ## 📚 Daily Learning

    ## 🧠 Notes

    """

    public static func instantiate(day: JournalDay, template: String = initial) throws -> String {
        let result = template.replacingOccurrences(of: "{{date}}", with: day.value)
            .replacingOccurrences(of: "{{title}}", with: day.value)
        guard !result.contains("{{"), !result.contains("}}"), !result.contains("<%") else {
            throw JournalError.invalidTemplate
        }
        return result
    }
}
