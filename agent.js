'use strict';
// UIKit target/action and UIKeyInput approach adapted from moppy-auto/agent.js.
// Uses ObjC supplied by driver.py; all UI access is dispatched to the main queue.
if (!ObjC.available) throw new Error('Objective-C runtime unavailable.');

function onMain(fn) {
    return new Promise((resolve, reject) => ObjC.schedule(ObjC.mainQueue, () => {
        try { resolve(fn()); } catch (error) { reject(error); }
    }));
}
function string(value) { return value ? value.toString() : null; }
function is(view, name) { return view.isKindOfClass_(ObjC.classes[name]); }
function windows() {
    const list = ObjC.classes.UIApplication.sharedApplication().windows();
    const out = [];
    for (let i = 0; i < list.count(); i++) {
        const win = list.objectAtIndex_(i);
        if (!win.isHidden()) out.push(win);
    }
    return out;
}
function topController() {
    const wins = windows();
    const win = wins.find(w => w.isKeyWindow()) || wins[0];
    if (!win) throw new Error('No visible app window. Unlock and open AirWallet.');
    let vc = win.rootViewController();
    for (let i = 0; vc && i < 30; i++) {
        let next = vc.presentedViewController();
        if (!next && is(vc, 'UINavigationController')) next = vc.visibleViewController();
        if (!next && is(vc, 'UITabBarController')) next = vc.selectedViewController();
        if (!next) break;
        vc = next;
    }
    if (!vc) throw new Error('No root controller.');
    return vc;
}
function collect(vc) {
    const out = {buttons: [], fields: [], labels: [], pages: [], viewTree: []};
    function walk(view, depth) {
        if (depth > 60 || out.viewTree.length >= 500 || view.isHidden() || Number(view.alpha()) <= 0.01) return;
        out.viewTree.push({depth, cls: view.$className, accessibilityId: string(view.accessibilityIdentifier())});
        if (is(view, 'UITextField')) {
            out.fields.push(view);
            return; // Never collect labels inside an input: they can contain its value.
        }
        else if (is(view, 'UIButton')) out.buttons.push(view);
        else if (is(view, 'UILabel')) {
            const text = string(view.text());
            if (text && !out.labels.includes(text)) out.labels.push(text);
        }
        if (is(view, 'UIPageControl')) out.pages.push({current: Number(view.currentPage()), count: Number(view.numberOfPages())});
        const children = view.subviews();
        for (let i = 0; i < children.count(); i++) walk(children.objectAtIndex_(i), depth + 1);
    }
    walk(vc.view(), 0);
    return out;
}
function snapshot() {
    const vc = topController();
    const elements = collect(vc);
    const bundle = ObjC.classes.NSBundle.mainBundle();
    let alert = null;
    if (is(vc, 'UIAlertController')) {
        const list = vc.actions();
        const titles = [];
        for (let i = 0; i < list.count(); i++) titles.push(string(list.objectAtIndex_(i).title()));
        alert = {title: string(vc.title()), message: string(vc.message()), buttons: titles};
    }
    return {
        bundle: string(bundle.bundleIdentifier()),
        version: string(bundle.objectForInfoDictionaryKey_('CFBundleShortVersionString')),
        topVC: vc.$className,
        title: string(vc.title()) || string(vc.navigationItem().title()),
        alert,
        pages: elements.pages,
        labels: elements.labels,
        viewTree: elements.viewTree,
        fields: elements.fields.map((field, index) => ({
            index, cls: field.$className, kind: 'text',
            placeholder: string(field.placeholder()), accessibilityId: string(field.accessibilityIdentifier()),
            secure: !!field.isSecureTextEntry(),
            filled: Number(field.text() ? field.text().length() : 0) > 0
        })),
        buttons: elements.buttons.map((button, index) => {
            const targets = button.allTargets().allObjects();
            const actions = [];
            for (let i = 0; i < targets.count(); i++) {
                const target = targets.objectAtIndex_(i);
                const selectors = button.actionsForTarget_forControlEvent_(target, 64);
                actions.push({target: target.$className, selectors: string(selectors)});
            }
            return {index, cls: button.$className, title: string(button.currentTitle()),
                accessibilityId: string(button.accessibilityIdentifier()), actions,
                enabled: !!button.isEnabled() && !!button.isUserInteractionEnabled()};
        })
    };
}
function requireScreen(expectedVC) {
    const vc = topController();
    if (vc.$className !== expectedVC) throw new Error('Screen changed: expected ' + expectedVC + ', got ' + vc.$className);
    return vc;
}
rpc.exports = {
    snapshot: () => onMain(snapshot),
    chooseAlert: (title, expectedMessage) => onMain(() => {
        const vc = topController();
        if (!is(vc, 'UIAlertController') || string(vc.message()) !== expectedMessage) {
            throw new Error('Alert changed; no action selected.');
        }
        const actions = vc.actions();
        const matches = [];
        for (let i = 0; i < actions.count(); i++) {
            const action = actions.objectAtIndex_(i);
            if (string(action.title()) === title && action.isEnabled()) matches.push(action);
        }
        if (matches.length !== 1) throw new Error('Expected one enabled alert action: ' + title);
        if (!vc.$methods.includes('- _dismissWithAction:')) {
            throw new Error('Observed UIKit alert selection method unavailable.');
        }
        vc['- _dismissWithAction:'](matches[0]);
        return true;
    }),
    setBirthday: (year, month, day, expectedVC) => onMain(() => {
        const vc = requireScreen(expectedVC);
        const field = collect(vc).fields[0];
        const picker = field ? field.inputView() : null;
        if (!picker || !is(picker, 'UIDatePicker')) throw new Error('Expected birthday UIDatePicker.');
        if (!field.becomeFirstResponder()) throw new Error('Cannot focus birthday input.');
        const calendar = (picker.calendar() || ObjC.classes.NSCalendar.currentCalendar()).copy();
        const zone = picker.timeZone();
        if (zone) calendar.setTimeZone_(zone);
        const parts = ObjC.classes.NSDateComponents.alloc().init();
        parts.setYear_(year); parts.setMonth_(month); parts.setDay_(day); parts.setHour_(12);
        if (zone) parts.setTimeZone_(zone);
        const date = calendar.dateFromComponents_(parts);
        if (!date) throw new Error('Invalid birthday.');
        picker.setDate_animated_(date, false);
        picker.sendActionsForControlEvents_(4096);
        field.resignFirstResponder();
        const actual = calendar.components_fromDate_(28, picker.date());
        return Number(actual.year()) === year && Number(actual.month()) === month &&
            Number(actual.day()) === day && !!string(field.text());
    }),
    enterPin: async (pin, expectedTitle) => {
        const controllerHandle = await onMain(() => {
        const vc = requireScreen('Sgc.PasscodeSetupViewController');
        if (string(vc.title()) !== expectedTitle || !/^[0-9]{4}$/.test(pin)) {
            throw new Error('Unexpected PIN step or invalid PIN.');
        }
        const indicators = [];
        function findIndicators(view) {
            if (view.isHidden()) return;
            if (is(view, 'UILabel')) {
                const text = string(view.text());
                if (text === '●' || text === '○' || text === '•') indicators.push(text);
            }
            const children = view.subviews();
            for (let i = 0; i < children.count(); i++) findIndicators(children.objectAtIndex_(i));
        }
        findIndicators(vc.view());
        if (indicators.some(text => text !== '○')) throw new Error('PIN entry is not empty; clear it before resuming.');
        const buttons = collect(vc).buttons;
        for (const digit of pin) {
            const matches = buttons.filter(button => string(button.currentTitle()) === digit && button.isEnabled());
            if (matches.length !== 1) throw new Error('PIN digit button missing or ambiguous.');
        }
        return vc.handle.toString();
        });
        for (let index = 0; index < pin.length; index++) {
            await onMain(() => {
            const vc = requireScreen('Sgc.PasscodeSetupViewController');
            if (vc.handle.toString() !== controllerHandle || string(vc.title()) !== expectedTitle) {
                throw new Error('PIN step changed before entry finished.');
            }
            const matches = collect(vc).buttons.filter(button =>
                string(button.currentTitle()) === pin[index] && button.isEnabled() && button.isUserInteractionEnabled());
            if (matches.length !== 1) throw new Error('PIN digit button missing or ambiguous.');
            matches[0].sendActionsForControlEvents_(64);
            });
            if (index < pin.length - 1) await new Promise(resolve => setTimeout(resolve, 200));
        }
        return true;
    },
    tapButton: (title, expectedVC) => onMain(() => {
        const vc = requireScreen(expectedVC);
        if (is(vc, 'UIAlertController')) throw new Error('Inspect the alert before continuing.');
        const matches = collect(vc).buttons.filter(button =>
            string(button.currentTitle()) === title && button.isEnabled() && button.isUserInteractionEnabled());
        if (matches.length !== 1) throw new Error('Expected exactly one enabled button with title ' + title + '; found ' + matches.length);
        matches[0].sendActionsForControlEvents_(64);
        return true;
    }),
    typeField: (index, value, expectedVC) => onMain(() => {
        const vc = requireScreen(expectedVC);
        const field = collect(vc).fields[index];
        if (!field || !field.isEnabled()) throw new Error('Input field not found or disabled.');
        if (!field.becomeFirstResponder()) throw new Error('Cannot focus the input field.');
        const length = Number(field.text() ? field.text().length() : 0);
        for (let i = 0; i < length; i++) field.deleteBackward();
        for (const character of value) {
            field.insertText_(character);
            field.sendActionsForControlEvents_(65536);
        }
        field.sendActionsForControlEvents_(262144);
        field.resignFirstResponder();
        return string(field.text()) === value;
    })
};
