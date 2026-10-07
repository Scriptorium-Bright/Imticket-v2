package org.example.ticket.reservation;

import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.member.domain.Member;
import org.example.ticket.member.domain.MemberRole;
import org.example.ticket.member.repository.MemberRepository;
import org.example.ticket.payment.domain.PaymentOrderStatus;
import org.example.ticket.payment.dto.*;
import org.example.ticket.payment.service.PaymentPreparationService;
import org.example.ticket.payment.service.PaymentVerificationService;
import org.example.ticket.performance.domain.Performance;
import org.example.ticket.performance.domain.PerformanceTime;
import org.example.ticket.performance.repository.PerformanceRepository;
import org.example.ticket.performance.repository.PerformanceTimeRepository;
import org.example.ticket.reservation.domain.ReservationStatus;
import org.example.ticket.reservation.dto.ReservationRequest;
import org.example.ticket.reservation.dto.ReservationResponse;
import org.example.ticket.reservation.service.ReservationBookingService;
import org.example.ticket.seat.domain.Seat;
import org.example.ticket.seat.domain.SeatStatus;
import org.example.ticket.seat.repository.SeatRepository;
import org.example.ticket.venue.domain.SeatGrade;
import org.example.ticket.venue.domain.VenueHall;
import org.example.ticket.venue.repository.VenueHallRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@ActiveProfiles("test")
@SpringBootTest
class ReservationPaymentFlowIntegrationTest {

    @Autowired private MemberRepository memberRepository;
    @Autowired private VenueHallRepository venueHallRepository;
    @Autowired private PerformanceRepository performanceRepository;
    @Autowired private PerformanceTimeRepository performanceTimeRepository;
    @Autowired private SeatRepository seatRepository;
    @Autowired private ReservationBookingService bookingService;
    @Autowired private PaymentPreparationService paymentPreparationService;
    @Autowired private PaymentVerificationService paymentVerificationService;

    @Test
    void 동일_멱등키는_같은_예약을_반환하고_다른_사용자는_선점된_좌석을_예약할_수_없다() {
        Fixture fixture = createFixture();
        Member other = createMember();

        ReservationRequest request = new ReservationRequest(
                fixture.performanceTime().getId(),
                List.of(fixture.seat().getId())
        );

        ReservationResponse first = bookingService.preReserve(
                fixture.member().getId(),
                "reservation-" + UUID.randomUUID(),
                request
        );
        String replayKey = "same-key-" + UUID.randomUUID();
        Seat secondSeat = createSecondSeat(fixture);
        ReservationRequest secondRequest = new ReservationRequest(
                fixture.performanceTime().getId(),
                List.of(secondSeat.getId())
        );
        ReservationResponse sameKeyFirst = bookingService.preReserve(
                fixture.member().getId(),
                replayKey,
                secondRequest
        );
        ReservationResponse sameKeyReplay = bookingService.preReserve(
                fixture.member().getId(),
                replayKey,
                secondRequest
        );

        assertThat(first.status()).isEqualTo(ReservationStatus.PENDING_PAYMENT);
        assertThat(sameKeyReplay.id()).isEqualTo(sameKeyFirst.id());

        assertThatThrownBy(() -> bookingService.preReserve(
                other.getId(),
                "other-" + UUID.randomUUID(),
                request
        ))
                .isInstanceOf(BusinessException.class)
                .extracting("code")
                .isEqualTo("SEAT_UNAVAILABLE");
    }

    @Test
    void 결제_승인_검증이_완료되면_예약과_좌석이_확정된다() {
        Fixture fixture = createFixture();
        ReservationResponse reservation = bookingService.preReserve(
                fixture.member().getId(),
                "reservation-" + UUID.randomUUID(),
                new ReservationRequest(fixture.performanceTime().getId(), List.of(fixture.seat().getId()))
        );

        PaymentPrepareResponse prepared = paymentPreparationService.prepare(
                fixture.member().getId(),
                "payment-" + UUID.randomUUID(),
                new PaymentPrepareRequest(reservation.id())
        );
        PaymentVerificationResponse verified = paymentVerificationService.verify(
                fixture.member().getId(),
                prepared.paymentOrderId(),
                new PaymentVerifyRequest(prepared.providerPaymentId())
        );

        assertThat(verified.paymentStatus()).isEqualTo(PaymentOrderStatus.APPLIED);
        assertThat(verified.reservationStatus()).isEqualTo(ReservationStatus.CONFIRMED);
        assertThat(seatRepository.findById(fixture.seat().getId()).orElseThrow().getStatus())
                .isEqualTo(SeatStatus.RESERVED);
    }

    private Fixture createFixture() {
        Member member = createMember();
        VenueHall hall = venueHallRepository.save(VenueHall.builder()
                .name("hall-" + UUID.randomUUID())
                .build());
        Performance performance = performanceRepository.save(Performance.builder()
                .title("performance-" + UUID.randomUUID())
                .build());
        PerformanceTime performanceTime = performanceTimeRepository.save(PerformanceTime.builder()
                .performance(performance)
                .venueHall(hall)
                .startsAt(LocalDateTime.now().plusDays(1))
                .build());
        Seat seat = seatRepository.save(Seat.builder()
                .performanceTime(performanceTime)
                .section("A")
                .rowNumber(1)
                .seatNumber(1)
                .grade(SeatGrade.R)
                .price(10000)
                .status(SeatStatus.AVAILABLE)
                .build());
        return new Fixture(member, performanceTime, seat);
    }

    private Member createMember() {
        return memberRepository.save(Member.builder()
                .username("member-" + UUID.randomUUID())
                .passwordHash("not-used-in-service-test")
                .role(MemberRole.MEMBER)
                .build());
    }

    private Seat createSecondSeat(Fixture fixture) {
        return createSeat(fixture, 2);
    }

    private Seat createSeat(Fixture fixture, int number) {
        return seatRepository.save(Seat.builder()
                .performanceTime(fixture.performanceTime())
                .section("A")
                .rowNumber(1)
                .seatNumber(number)
                .grade(SeatGrade.R)
                .price(10000)
                .status(SeatStatus.AVAILABLE)
                .build());
    }

    private record Fixture(Member member, PerformanceTime performanceTime, Seat seat) {
    }
}
