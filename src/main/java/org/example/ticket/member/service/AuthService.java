package org.example.ticket.member.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.member.domain.Member;
import org.example.ticket.member.domain.MemberRole;
import org.example.ticket.member.dto.LoginRequest;
import org.example.ticket.member.dto.RegisterRequest;
import org.example.ticket.member.dto.TokenResponse;
import org.example.ticket.member.repository.MemberRepository;
import org.example.ticket.security.JwtService;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class AuthService {

    private final MemberRepository memberRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    @Transactional
    public TokenResponse register(RegisterRequest request) {
        if (memberRepository.existsByUsername(request.username())) {
            throw new BusinessException(HttpStatus.CONFLICT, "USERNAME_DUPLICATED", "이미 사용 중인 아이디입니다.");
        }

        Member member = memberRepository.save(Member.builder()
                .username(request.username())
                .passwordHash(passwordEncoder.encode(request.password()))
                .role(MemberRole.MEMBER)
                .build());
        return token(member);
    }

    @Transactional(readOnly = true)
    public TokenResponse login(LoginRequest request) {
        Member member = memberRepository.findByUsername(request.username())
                .orElseThrow(() -> new BusinessException(
                        HttpStatus.UNAUTHORIZED, "LOGIN_FAILED", "아이디 또는 비밀번호가 올바르지 않습니다."
                ));
        if (!passwordEncoder.matches(request.password(), member.getPasswordHash())) {
            throw new BusinessException(
                    HttpStatus.UNAUTHORIZED, "LOGIN_FAILED", "아이디 또는 비밀번호가 올바르지 않습니다."
            );
        }
        return token(member);
    }

    private TokenResponse token(Member member) {
        return new TokenResponse(jwtService.issue(member), member.getId(), member.getRole().name());
    }
}
