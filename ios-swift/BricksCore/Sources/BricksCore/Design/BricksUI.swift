import SwiftUI
import UIKit

// MARK: — Theme

public struct BricksTheme {
    public let dark: Bool
    public init(dark: Bool = false) { self.dark = dark }

    public var bg:    Color { dark ? BricksColors.night     : BricksColors.paper }
    public var surf:  Color { dark ? BricksColors.nightSoft : .white }
    public var surf2: Color { dark ? BricksColors.nightDeep : BricksColors.paperSoft }
    public var hair:  Color { dark ? BricksColors.nightHair : BricksColors.hair }
    public var hairS: Color { dark ? BricksColors.nightHairS : BricksColors.hairStrong }
    public var ink:   Color { dark ? BricksColors.moon      : BricksColors.ink }
    public var ink2:  Color { dark ? BricksColors.moonSoft  : BricksColors.inkSoft }
    public var ink3:  Color { dark ? BricksColors.moonMute  : BricksColors.inkMute }
    public var ink4:  Color { dark ? BricksColors.moonFaint : BricksColors.inkFaint }
}

// MARK: — Eyebrow label (Over)

public struct BOver: View {
    let text: String
    public var color: Color
    public init(_ text: String, color: Color = BricksColors.inkMute) {
        self.text = text
        self.color = color
    }
    public var body: some View {
        Text(text.uppercased())
            .font(BricksFont.mono(10.5))
            .kerning(1.8)
            .foregroundStyle(color)
    }
}

// MARK: — Pill

public enum BricksPillTone {
    case brick, ok, warn, neutral, ink
}

public struct BPill: View {
    let text: String
    let tone: BricksPillTone
    let dark: Bool

    public init(_ text: String, tone: BricksPillTone = .neutral, dark: Bool = false) {
        self.text = text; self.tone = tone; self.dark = dark
    }

    var bg: Color {
        switch tone {
        case .brick:   return dark ? BricksColors.brickSoft.opacity(0.13) : BricksColors.brickTint
        case .ok:      return dark ? Color(hex: "E6ECDD").opacity(0.13) : BricksColors.okTint
        case .warn:    return dark ? Color(hex: "F6E8D1").opacity(0.13) : BricksColors.warnTint
        case .neutral: return dark ? BricksColors.nightDeep : BricksColors.paperSoft
        case .ink:     return dark ? BricksColors.moon : BricksColors.ink
        }
    }
    var fg: Color {
        switch tone {
        case .brick:   return dark ? BricksColors.brickSoft : BricksColors.brickDeep
        case .ok:      return dark ? Color(hex: "B8CC9E") : BricksColors.ok
        case .warn:    return dark ? Color(hex: "E6B477") : BricksColors.warn
        case .neutral: return dark ? BricksColors.moonSoft : BricksColors.inkSoft
        case .ink:     return dark ? BricksColors.night : BricksColors.paper
        }
    }

    public var body: some View {
        Text(text.uppercased())
            .font(BricksFont.mono(10))
            .kerning(1.4)
            .foregroundStyle(fg)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(bg)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

// MARK: — Hairline divider

public struct BHair: View {
    let dark: Bool
    public init(dark: Bool = false) { self.dark = dark }
    public var body: some View {
        Rectangle()
            .fill(dark ? BricksColors.nightHair : BricksColors.hair)
            .frame(height: 1)
    }
}

// MARK: — Stat cell (used in 3-column stat bars)

public struct BStatCell: View {
    let label: String
    let value: String
    let dark: Bool
    public init(_ label: String, _ value: String, dark: Bool = false) {
        self.label = label; self.value = value; self.dark = dark
    }
    var th: BricksTheme { BricksTheme(dark: dark) }
    public var body: some View {
        VStack(alignment: .center, spacing: 4) {
            Text(value)
                .font(BricksFont.mono(20))
                .foregroundStyle(th.ink)
            BOver(label, color: th.ink4)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity)
        .background(th.surf)
    }
}

// MARK: — Stat bar (3-column)

public struct BStatBar<Content: View>: View {
    let dark: Bool
    @ViewBuilder let content: () -> Content
    public init(dark: Bool = false, @ViewBuilder content: @escaping () -> Content) {
        self.dark = dark; self.content = content
    }
    public var body: some View {
        HStack(spacing: 1) {
            content()
        }
        .background(dark ? BricksColors.nightHair : BricksColors.hair)
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .strokeBorder(dark ? BricksColors.nightHair : BricksColors.hair, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

// MARK: — Tab bar

public struct BricksTabItem {
    public let sfSymbol: String
    public let label: String
    public init(_ sfSymbol: String, _ label: String) {
        self.sfSymbol = sfSymbol; self.label = label
    }
}

public struct BricksTabBar: View {
    let tabs: [BricksTabItem]
    @Binding var selected: Int
    let dark: Bool

    public init(tabs: [BricksTabItem], selected: Binding<Int>, dark: Bool = false) {
        self.tabs = tabs; self._selected = selected; self.dark = dark
    }

    var th: BricksTheme { BricksTheme(dark: dark) }

    public var body: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(th.hair)
                .frame(height: 1)
            HStack(spacing: 0) {
                ForEach(tabs.indices, id: \.self) { i in
                    let active = i == selected
                    Button { selected = i } label: {
                        VStack(spacing: 4) {
                            Rectangle()
                                .fill(active ? BricksColors.brick : Color.clear)
                                .frame(width: 24, height: 2)
                            Image(systemName: tabs[i].sfSymbol)
                                .font(.system(size: 19, weight: active ? .semibold : .regular))
                                .foregroundStyle(active ? th.ink : th.ink4)
                            Text(tabs[i].label.uppercased())
                                .font(BricksFont.mono(10))
                                .kerning(1.2)
                                .foregroundStyle(active ? th.ink : th.ink4)
                        }
                        .padding(.vertical, 8)
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.bottom, 20)
            .background(th.bg)
        }
    }
}

// MARK: — Section card container

public struct BCard<Content: View>: View {
    let dark: Bool
    let padding: CGFloat
    @ViewBuilder let content: () -> Content
    public init(dark: Bool = false, padding: CGFloat = 0, @ViewBuilder content: @escaping () -> Content) {
        self.dark = dark; self.padding = padding; self.content = content
    }
    var th: BricksTheme { BricksTheme(dark: dark) }
    public var body: some View {
        content()
            .padding(padding > 0 ? padding : 0)
            .background(th.surf)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(th.hair, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

// MARK: — Avatar initials

public struct BAvatar: View {
    let initials: String
    let size: CGFloat
    let dark: Bool
    public init(_ initials: String, size: CGFloat = 36, dark: Bool = false) {
        self.initials = initials; self.size = size; self.dark = dark
    }
    public var body: some View {
        Text(initials.prefix(2).uppercased())
            .font(.system(size: size * 0.33, weight: .semibold))
            .foregroundStyle(BricksColors.brickDeep)
            .frame(width: size, height: size)
            .background(BricksColors.brickSoft)
            .clipShape(RoundedRectangle(cornerRadius: 2))
    }
}

// MARK: — Primary button

public struct BBtn: View {
    let label: String
    let sfSymbol: String?
    let variant: BtnVariant
    let dark: Bool
    let full: Bool
    let action: () -> Void

    public enum BtnVariant { case primary, accent, ghost, secondary }

    public init(_ label: String, symbol: String? = nil, variant: BtnVariant = .primary, dark: Bool = false, full: Bool = false, action: @escaping () -> Void) {
        self.label = label; self.sfSymbol = symbol; self.variant = variant; self.dark = dark; self.full = full; self.action = action
    }

    var th: BricksTheme { BricksTheme(dark: dark) }

    var bg: Color {
        switch variant {
        case .primary:   return th.ink
        case .accent:    return BricksColors.brick
        case .ghost:     return .clear
        case .secondary: return th.surf2
        }
    }
    var fg: Color {
        switch variant {
        case .primary:   return th.bg
        case .accent:    return .white
        case .ghost:     return th.ink
        case .secondary: return th.ink
        }
    }
    var border: Color {
        switch variant {
        case .ghost:     return th.hairS
        default:         return .clear
        }
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if full { Spacer(minLength: 0) }
                Text(label)
                    .font(.system(size: 14, weight: .medium))
                if let sym = sfSymbol {
                    Image(systemName: sym)
                        .font(.system(size: 13))
                }
                if full { Spacer(minLength: 0) }
            }
            .foregroundStyle(fg)
            .padding(.horizontal, 20)
            .padding(.vertical, 13)
            .frame(maxWidth: full ? .infinity : nil)
            .background(bg)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(border, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 2))
        }
    }
}

// MARK: — Labelled text field (mirrors components/core/Field.jsx)

public struct BField: View {
    let label: String?
    let placeholder: String
    @Binding var text: String
    let isSecure: Bool
    let keyboardType: UIKeyboardType
    let textContentType: UITextContentType?
    let dark: Bool

    @FocusState private var focused: Bool

    public init(
        _ label: String? = nil,
        placeholder: String,
        text: Binding<String>,
        isSecure: Bool = false,
        keyboardType: UIKeyboardType = .default,
        textContentType: UITextContentType? = nil,
        dark: Bool = false
    ) {
        self.label = label
        self.placeholder = placeholder
        self._text = text
        self.isSecure = isSecure
        self.keyboardType = keyboardType
        self.textContentType = textContentType
        self.dark = dark
    }

    var th: BricksTheme { BricksTheme(dark: dark) }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let label {
                BOver(label, color: th.ink4)
            }
            Group {
                if isSecure {
                    SecureField(placeholder, text: $text)
                } else {
                    TextField(placeholder, text: $text)
                }
            }
            .focused($focused)
            .keyboardType(keyboardType)
            .textContentType(textContentType)
            .autocapitalization(.none)
            .font(.system(size: 15))
            .foregroundStyle(th.ink)
            .padding(14)
            .background(th.surf)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(focused ? BricksColors.brick : th.hairS, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 2))
        }
    }
}

// MARK: — Photo thumbnail (real property photo from bundle)

public struct BImg: View {
    let index: Int
    let height: CGFloat
    let dark: Bool
    public init(_ index: Int = 0, height: CGFloat = 160, dark: Bool = false) {
        self.index = index; self.height = height; self.dark = dark
    }
    public var body: some View {
        Group {
            if let img = photoImage(index) {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFill()
            } else {
                (dark ? BricksColors.nightSoft : BricksColors.paperDeep)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: height)
        .clipped()
    }

    private func photoImage(_ idx: Int) -> UIImage? {
        let name = String(format: "%02d.jpg", (idx % 9) + 1)
        guard let url = Bundle.module.url(forResource: name, withExtension: nil),
              let data = try? Data(contentsOf: url) else { return nil }
        return UIImage(data: data)
    }
}
